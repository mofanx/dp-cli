#!/usr/bin/env python3
"""hard_cases.jsonl 复核清单——主动学习 LLM 标注输入。

decide 低置信候选由 dp_cli.decide_train._maybe_log_hard_case 被动
落盘到 ~/.dp_cli/hard_cases.jsonl（conf<0.5 或 top2 margin<0.1；
另有 conf≥0.8+post_delta=[] 高置信零效果反查通道，
默认开、DPX_HARD_CASES=0 关）。本脚本读 jsonl 汇总条数/conf 分布/
触发形态/高频难点，输出 review 清单供 LLM 定期标注（标注后人工
核验进 teacher/quarantine 流水线，不直接入训）。

用法：
    python bench/review_hard_cases.py                  # 摘要 → stdout
    python bench/review_hard_cases.py --json           # 摘要 JSON
    python bench/review_hard_cases.py --emit bench/hard_review.jsonl
    python bench/review_hard_cases.py --emit-teacher bench/teacher_pending.jsonl
    python bench/review_hard_cases.py --file x.jsonl --limit 30
    python bench/review_hard_cases.py --promote-teacher bench/teacher_pending.jsonl \
        --promote-out bench/teacher_labeled.jsonl      # 转正确认门

--emit-teacher 出 teacher 待标注模板（chosen_ref/positive/negatives
留空 prov='teacher_pending'）——LLM 回填后须过 --promote-teacher
显式确认门转正 prov='teacher'（chosen_ref 已填+∈candidate_refs+
negatives 非空三条件齐备，positive 留空按 chosen 候选回填），再
过泄漏三闸方可入训；手动改 prov 不写 chosen_ref/negatives 的伪
标注过不了本门，未回填/未转正前 eligible() 同样拒收。三形态
数据契约见 bench/DATA_SCHEMA.md。
"""
import argparse
import json
from collections import Counter
from pathlib import Path

DEFAULT_FILE = Path.home() / '.dp_cli' / 'hard_cases.jsonl'

_CONF_BUCKETS = [(0.10, '<0.10'), (0.25, '0.10-0.25'),
                 (0.40, '0.25-0.40'), (0.50, '0.40-0.50'),
                 (float('inf'), '>=0.50(纯 margin 触发)')]
_MARGIN_BUCKETS = [(0.02, '<0.02'), (0.05, '0.02-0.05'),
                   (0.10, '0.05-0.10'),
                   (float('inf'), '>=0.10(纯 conf 触发)')]
_TEACHER_CAND_MAX = 32   # teacher 模板候选上限——v2 全池行 LLM 输入防爆


def _bucket(v, edges):
    v = v if isinstance(v, (int, float)) else 0.0
    for hi, label in edges:
        if v < hi:
            return label
    return edges[-1][1]


def load_rows(path):
    """jsonl → rows；坏行计数不炸（写端并发 trim 可能留残行）。"""
    rows, bad = [], 0
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return rows, bad
    for ln in lines:
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            bad += 1
    return rows, bad


def summarize(rows):
    """条数/conf 分布/触发形态/引擎/schema_v/prov 分布/高频
    goal+url（miss 形态代理）。"""
    conf_b, marg_b, trig, eng, msrc, sv, prov = (
        Counter() for _ in range(7))
    goals, urls, unsure = Counter(), Counter(), 0
    for r in rows:
        conf_b[_bucket(r.get('conf'), _CONF_BUCKETS)] += 1
        marg_b[_bucket(r.get('margin'), _MARGIN_BUCKETS)] += 1
        for t in r.get('triggers') or ['unknown']:
            trig[t] += 1
        eng[r.get('engine') or '?'] += 1
        msrc[r.get('margin_source') or '?'] += 1
        sv[f"v{r.get('schema_v') or 1}"] += 1
        prov[r.get('prov') or 'passive'] += 1   # 无 prov=被动落盘行
        unsure += bool(r.get('unsure'))
        if r.get('goal'):
            goals[r['goal']] += 1
        if r.get('url'):
            urls[r['url']] += 1
    return {'total': len(rows), 'unsure': unsure,
            'conf_buckets': dict(conf_b),
            'margin_buckets': dict(marg_b),
            'triggers': dict(trig), 'engines': dict(eng),
            'margin_source': dict(msrc),
            'schema_v': dict(sv), 'prov': dict(prov),
            'top_goals': goals.most_common(10),
            'top_urls': urls.most_common(10)}


def review_rows(rows, limit=None):
    """按 conf→margin 升序排——最低置信优先标注；裁剪为 LLM 输入形态
    （goal/候选 ref+name+score/引擎 pick/conf/margin/触发原因/页面 url）。"""

    def _key(r):
        c, m = r.get('conf'), r.get('margin')
        return (c if isinstance(c, (int, float)) else 1.0,
                m if isinstance(m, (int, float)) else 1.0)

    out = []
    for r in sorted(rows, key=_key):
        out.append({'goal': r.get('goal'), 'url': r.get('url'),
                    'schema_v': r.get('schema_v'),
                    'engine_ref': r.get('engine_ref'),
                    'conf': r.get('conf'), 'margin': r.get('margin'),
                    'margin_source': r.get('margin_source'),
                    'triggers': r.get('triggers'),
                    'unsure': r.get('unsure'),
                    # escalate 反哺行透传（schema_v=2 可选字段）——
                    # prov='escalate_feedback' 行带已确认 chosen_ref
                    # +可选 post_state 证据，复核清单可辨来源
                    'prov': r.get('prov'),
                    'feedback': r.get('feedback'),
                    'chosen_ref': r.get('chosen_ref'),
                    'post_state': r.get('post_state'),
                    'candidates': r.get('candidates'),
                    'ts': r.get('ts'), 'session': r.get('session')})
        if limit and len(out) >= limit:
            break
    return out


def _pick_teacher_cands(cands, max_n=_TEACHER_CAND_MAX):
    """hard_case candidates → teacher 模板候选子集（v 分流取样）。

    v1 行（无 schema_v=top3 截断）与小池行原样保留；v2 全池行
    超 max_n 时按干扰度取样而非纯前 N：
      1. 计分候选（score 非空）按分降序——歧义焦点必保
      2. 池内未计分候选与计分集同名者——同标签高干扰优先
      3. 与计分集同 role 者——同形态干扰次优先
      4. 其余按池序补足至 max_n
    """
    if len(cands) <= max_n:
        return list(cands)
    scored = sorted((c for c in cands if c.get('score') is not None),
                    key=lambda c: -c['score'])
    rest = [c for c in cands if c.get('score') is None]
    names = {c.get('name') for c in scored if c.get('name')}
    roles = {c.get('role') for c in scored if c.get('role')}
    same_name = [c for c in rest if c.get('name') in names]
    same_role = [c for c in rest
                 if c.get('name') not in names
                 and c.get('role') in roles]
    other = [c for c in rest
             if c.get('name') not in names
             and c.get('role') not in roles]
    return (scored + same_name + same_role + other)[:max_n]


def teacher_rows(rows, limit=None):
    """review 清单 → teacher 待标注模板行（hard_case→teacher 转正接口）。

    hard_case 本身不是标签是待标注池：模板行 prov='teacher_pending'
    标未标注态——chosen_ref/positive/negatives 三字段留空待 LLM
    回填，回填后必须经 promote_teacher() 显式确认门转正 prov 改
    'teacher' 再过泄漏三闸才可入训；未回填/未转正前 eligible()
    拒收（src 非 verified_real），绝不误入默认训练。candidates
    保留 ref+文本双形态供标注对照，engine_ref/conf/margin/
    triggers 透传供标注参考（不作标签）。schema_v=2 全池行候选
    经 _pick_teacher_cands 干扰度取样至 _TEACHER_CAND_MAX——
    计分候选+同名/同 role 高干扰优先，非纯前 N 截断。
    """
    out = []
    for r in review_rows(rows, limit=limit):
        cands = _pick_teacher_cands(r.get('candidates') or [])
        out.append({'src': 'teacher_hardneg',
                    'prov': 'teacher_pending',   # 待标注态≠已标注 teacher_*
                    'schema_v': r.get('schema_v') or 1,
                    'page': r.get('url'),
                    'goal': r.get('goal'),
                    'query': r.get('goal'),
                    'chosen_ref': None,          # ← LLM 标注位
                    'positive': None,            # ← 由 chosen_ref 回填
                    'negatives': None,           # ← 除 chosen 外候选回填
                    'candidates': [
                        f"{c.get('role', '')} {c.get('name', '')}".strip()
                        for c in cands],
                    'candidate_refs': [c.get('ref') for c in cands],
                    'engine_ref': r.get('engine_ref'),
                    'conf': r.get('conf'),
                    'margin': r.get('margin'),
                    'triggers': r.get('triggers'),
                    'session': r.get('session'),
                    'ts': r.get('ts'),
                    'teacher_note': 'PENDING: 填 chosen_ref 后回填 '
                                    'positive/negatives'})
    return out


def promote_teacher(rows):
    """teacher_pending → teacher 显式确认门（唯一转正接口）。

    手动把 prov 从 'teacher_pending' 改成 'teacher' 不能绕过标注——
    转正必须过本门校验：
      1. prov=='teacher_pending'（已转正/异源行不重入）
      2. chosen_ref 已填且 ∈ candidate_refs（数字字符串可 int 归一）
      3. negatives 非空 list
      4. query 非空 且 最终 positive ∉ negatives（防自相矛盾行）
    全过才写 prov='teacher'；positive 留空时按 chosen 候选渲染
    回填。返回 (promoted, rejected)，rejected 为 (row, reason)
    二元组列表——chosen_ref 空/非法一律拒收计数，绝不静默放行。
    """
    promoted, rejected = [], []
    for r in rows:
        def _rej(why):
            rejected.append((r, why))
        if not isinstance(r, dict) or r.get('prov') != 'teacher_pending':
            _rej('prov 非 teacher_pending')
            continue
        ref = r.get('chosen_ref')
        if ref is None or (isinstance(ref, str) and not ref.strip()):
            _rej('chosen_ref 空')
            continue
        cand_refs = r.get('candidate_refs')
        if not isinstance(cand_refs, list):
            cand_refs = []
        hit = ref if ref in cand_refs else None
        # LLM 回填可能把 ref 写成 "5" 字符串——数字串 int 归一再比对
        if hit is None and isinstance(ref, str) and ref.strip().isdigit():
            iv = int(ref.strip())
            hit = iv if iv in cand_refs else None
        if hit is None:
            _rej('chosen_ref 不在 candidate_refs')
            continue
        negs = r.get('negatives')
        if not isinstance(negs, list) or not negs:
            _rej('negatives 空')
            continue
        if not isinstance(r.get('query'), str) or not r['query'].strip():
            _rej('query 空')
            continue
        out = dict(r)
        out['chosen_ref'] = hit
        if not out.get('positive'):
            cands = r.get('candidates') or []
            try:
                out['positive'] = cands[cand_refs.index(hit)]
            except (ValueError, IndexError):
                out['positive'] = None
        if not out.get('positive') or out['positive'] in negs:
            _rej('positive 缺失或在 negatives 中')
            continue
        out['prov'] = 'teacher'
        out['teacher_note'] = ('promoted: --promote-teacher 门检通过，'
                               '入训前仍须过泄漏三闸+--allow-unverified')
        promoted.append(out)
    return promoted, rejected


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='hard_cases.jsonl 复核清单——LLM 标注输入')
    ap.add_argument('--file', default=str(DEFAULT_FILE),
                    help='hard_cases jsonl 路径（默认 ~/.dp_cli/）')
    ap.add_argument('--limit', type=int, default=100,
                    help='review 清单条数上限（conf 升序取头部）')
    ap.add_argument('--emit', default=None,
                    help='review 清单 jsonl 落盘路径（缺省只打印摘要）')
    ap.add_argument('--emit-teacher', default=None,
                    help='teacher 待标注模板 jsonl 落盘路径'
                         '（chosen_ref/positive 留空待 LLM 填）')
    ap.add_argument('--promote-teacher', default=None,
                    help='teacher_pending jsonl 转正确认门——校验 '
                         'chosen_ref∈candidate_refs+negatives 非空才写 '
                         'prov=teacher（配 --promote-out 落盘；'
                         '缺省 dry-run 只报计数）')
    ap.add_argument('--promote-out', default=None,
                    help='--promote-teacher 产出的 teacher jsonl 路径')
    ap.add_argument('--json', action='store_true', help='摘要 JSON 输出')
    a = ap.parse_args(argv)
    rows, bad = load_rows(a.file)
    s = summarize(rows)
    if bad:
        s['unparsed_lines'] = bad
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(f"hard_cases: {s['total']} 条 ({a.file})"
              + (f'，{bad} 行无法解析' if bad else ''))
        if not s['total']:
            print('（无数据——decide 低置信时自动落盘，'
                  'DPX_HARD_CASES=0 可关）')
        else:
            print('conf 分布:', s['conf_buckets'])
            print('margin 分布:', s['margin_buckets'])
            print('触发形态:', s['triggers'],
                  f"（AGENT_UNSURE 行 {s['unsure']}）")
            print('引擎:', s['engines'],
                  '| margin_source:', s['margin_source'],
                  '| schema_v:', s['schema_v'])
            if s['top_goals']:
                print('高频 goal:', s['top_goals'][:5])
            if s['top_urls']:
                print('高频 url:', s['top_urls'][:5])
    if a.emit:
        rev = review_rows(rows, limit=a.limit)
        Path(a.emit).write_text(
            ''.join(json.dumps(r, ensure_ascii=False) + '\n'
                    for r in rev), encoding='utf-8')
        print(f'review 清单 {len(rev)} 条 → {a.emit}')
    if a.emit_teacher:
        tr = teacher_rows(rows, limit=a.limit)
        Path(a.emit_teacher).write_text(
            ''.join(json.dumps(r, ensure_ascii=False) + '\n'
                    for r in tr), encoding='utf-8')
        print(f'teacher 待标注模板 {len(tr)} 条 → {a.emit_teacher}'
              '（chosen_ref/positive 待 LLM 回填后过 '
              '--promote-teacher 转正+泄漏三闸入训）')
    if a.promote_teacher:
        prows, pbad = load_rows(a.promote_teacher)
        ok, no = promote_teacher(prows)
        if a.promote_out:
            Path(a.promote_out).write_text(
                ''.join(json.dumps(r, ensure_ascii=False) + '\n'
                        for r in ok), encoding='utf-8')
        print(f'promote: {len(ok)} 行转正 prov=teacher'
              + (f' → {a.promote_out}' if a.promote_out
                 else '（dry-run 未落盘，配 --promote-out 写出）')
              + f'，拒收 {len(no)} 行'
              + (f'，{pbad} 行无法解析' if pbad else ''))
        for r, why in no:
            tag = (r or {}).get('goal') or (r or {}).get('query') \
                if isinstance(r, dict) else r
            print(f'  reject: {why} — {tag}')


if __name__ == '__main__':
    main()
