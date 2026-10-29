#!/usr/bin/env python3
"""hard_cases.jsonl 复核清单——主动学习 LLM 标注输入。

decide 低置信候选由 dp_cli.decide_train._maybe_log_hard_case 被动
落盘到 ~/.dp_cli/hard_cases.jsonl（conf<0.5 或 top2 margin<0.1，
默认开、DPX_HARD_CASES=0 关）。本脚本读 jsonl 汇总条数/conf 分布/
触发形态/高频难点，输出 review 清单供 LLM 定期标注（标注后人工
核验进 teacher/quarantine 流水线，不直接入训）。

用法：
    python bench/review_hard_cases.py                  # 摘要 → stdout
    python bench/review_hard_cases.py --json           # 摘要 JSON
    python bench/review_hard_cases.py --emit bench/hard_review.jsonl
    python bench/review_hard_cases.py --file x.jsonl --limit 30
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
    """条数/conf 分布/触发形态/引擎分布/高频 goal+url（miss 形态代理）。"""
    conf_b, marg_b, trig, eng, msrc = (Counter() for _ in range(5))
    goals, urls, unsure = Counter(), Counter(), 0
    for r in rows:
        conf_b[_bucket(r.get('conf'), _CONF_BUCKETS)] += 1
        marg_b[_bucket(r.get('margin'), _MARGIN_BUCKETS)] += 1
        for t in r.get('triggers') or ['unknown']:
            trig[t] += 1
        eng[r.get('engine') or '?'] += 1
        msrc[r.get('margin_source') or '?'] += 1
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
                    'engine_ref': r.get('engine_ref'),
                    'conf': r.get('conf'), 'margin': r.get('margin'),
                    'margin_source': r.get('margin_source'),
                    'triggers': r.get('triggers'),
                    'unsure': r.get('unsure'),
                    'candidates': r.get('candidates'),
                    'ts': r.get('ts'), 'session': r.get('session')})
        if limit and len(out) >= limit:
            break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='hard_cases.jsonl 复核清单——LLM 标注输入')
    ap.add_argument('--file', default=str(DEFAULT_FILE),
                    help='hard_cases jsonl 路径（默认 ~/.dp_cli/）')
    ap.add_argument('--limit', type=int, default=100,
                    help='review 清单条数上限（conf 升序取头部）')
    ap.add_argument('--emit', default=None,
                    help='review 清单 jsonl 落盘路径（缺省只打印摘要）')
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
                  '| margin_source:', s['margin_source'])
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


if __name__ == '__main__':
    main()
