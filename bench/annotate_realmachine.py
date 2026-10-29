#!/usr/bin/env python3
"""真机 hard_cases 教师标注——一次性脚本（本 agent 即教师）。

输入 bench/realmachine_pending_all.jsonl（--emit-teacher 模板），
按 (goal,url) 去重取首行，逐组填 chosen_ref/negatives，
输出 bench/realmachine_teacher.jsonl（prov 仍 teacher_pending，
随后过 --promote-teacher 四条件门转正）。

标注原则：
- 同文本孪生候选（ambig_item 两行 删除/归档、github 两个 Sign up）
  正例取首个 ref；同文本孪生不进 negatives（同串正负双标签矛盾，
  promote 闸 4 也会拒），在 teacher_note 里登记。
- 正确元素不在 top3 候选内的行硬标跳过（chosen_ref 留空 →
  promote 拒收计数），不猜标。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_cases import EVAL_CASES          # noqa: E402
from bench_engines import rich_name        # noqa: E402
from merge_train import _hit               # noqa: E402

IN = Path(__file__).parent / 'realmachine_pending_all.jsonl'
OUT = Path(__file__).parent / 'realmachine_teacher.jsonl'

# (goal, url_basename) → (chosen_ref, negatives, note)；None = 硬标跳过
DECISIONS = {
    ('删除这条记录', 'ambig_item.html'): (
        3, ['button 归档'],
        '两行同名按钮组(查看/归档/删除/置顶×2)，ref3/ref7 同为 button 删除'
        '——孪生歧义场，取首行 ref3；ref7 同文本不入 negatives(同串双标签矛盾)'),
    ('在输入框填手机号', 'ambig_auth.html'): (
        4, ['textbox 密码', 'link 帮助'], '手机号或邮箱输入框唯一正例'),
    ('填写用户名', 'login.html'): (
        1, ['checkbox', 'button Sign In'], 'Username textbox 唯一正例'),
    ('填写邮箱地址', 'form.html'): (
        2, ['checkbox', 'textbox Phone'], 'Email textbox 唯一正例'),
    ('开启深色模式', 'ambig_check.html'): (
        2, ['button Save changes', 'button Reset to defaults'],
        'Dark mode checkbox 唯一正例'),
    ('开启深色模式', 'settings.html'): (
        None, [],
        'SKIP: 候选全无名(radio×2+checkbox 空 name)，Dark mode checkbox '
        'ref2 不在 top3 候选内，无法判正例'),
    ('归档这条记录', 'ambig_item.html'): (
        2, ['button 置顶'],
        'ref2/ref6 同为 button 归档——孪生取首行 ref2；ref6 同文本剔除'),
    ('找回忘记的密码', 'ambig_auth.html'): (
        8, ['textbox 密码', 'link 帮助'], '忘记密码 link 唯一正例'),
    ('提交订单', 'recall_bench.html'): (
        49, ['link 我的订单', 'link 查看历史订单'],
        '提交订单 button 唯一正例；订单族 link 为强近干扰'),
    ('搜索本站内容', 'nav.html'): (
        7, ['link Blog', 'link Products'], 'Search site searchbox 唯一正例'),
    ('查看耳机的详情', 'ambig_dup.html'): (
        3, ['button 查看手机详情', 'button 查看键盘详情'],
        '耳机详情 button 正例；手机/键盘详情为同族近干扰'),
    ('注册 GitHub 账号', 'github.com'): (
        7, ['link GitHub'],
        'Sign up for GitHub button 正例(ref7/ref44 同文本孪生取 ref7，'
        'ref44 剔除)；link GitHub logo 为引擎误选'),
    ('注册新账号', 'ambig_auth.html'): (
        7, ['textbox 手机号或邮箱', 'link 帮助'], '注册新账号 button 唯一正例'),
    ('点击 Sign in 登录', 'github.com'): (
        4, ['button Sign up for GitHub'],
        'Sign in link 正例；Sign up button×2(同文本去重)为方向干扰'),
    ('点击导出按钮', 'buttons.html'): (
        5, ['button 退出', 'button 帮助'], '导出 button 唯一正例'),
    ('点击登录按钮', 'login.html'): (
        None, [],
        'SKIP: 正例 button Sign In(ref4) 不在 top3 候选'
        '(Username/Password/checkbox)，候选内无正确答案不猜标'),
    ('点登录', 'ambig_auth.html'): (
        6, ['link 登录', 'textbox 手机号或邮箱'],
        '表单 submit button 登录 为动作正例；nav link 登录 同名异 role '
        '强干扰——歧义组核心价值样本'),
    ('立即购买下单', 'product.html'): (
        6, ['spinbutton Qty', 'combobox'], 'Buy now button 唯一正例'),
}

# 泄漏闸命中 → 隔离（chosen_ref 置空不标注，单独登记，不进 jsonl 主文件）
QUAR = {
    '点击导出按钮': 'gate2: positive button 导出 复用 miss-eval x01 候选串',
    '删除这条记录': 'gate2: positive button 删除 复用 miss-eval x05 候选串',
    '提交订单': "gate3: query 与 merge_out_v24 verified_real 行重复"
                "（且该行 positive=button 确认无误，同 query 跨页异正例冲突）",
}

EVAL_GOALS = {c['goal'].strip() for c in EVAL_CASES}
EVAL_OPTS_ALL = {f"{o['role']} {rich_name(o)}".strip()
                 for c in EVAL_CASES for o in c['options']}
# 历史 miss id（v19∪v24∪v25）候选串——同前两轮口径
MISS_IDS = {"t06", "c10", "n05", "n08", "n22", "m11", "m12", "m13", "m15",
            "m17", "m26", "m29", "m30", "x03", "x04", "x09", "x12", "x18",
            "x43", "m14", "m27", "x01", "m25", "m10", "x05", "x15", "x47",
            "x50"}
EVAL_OPTS_MISS = {f"{o['role']} {rich_name(o)}".strip()
                  for c in EVAL_CASES if c['id'] in MISS_IDS
                  for o in c['options']}
V24_QUERIES = {r.get('query', '').strip()
               for r in json.load(open(Path(__file__).parent /
                                       'merge_out_v24.json',
                                       encoding='utf-8'))}


def main():
    rows = [json.loads(l) for l in IN.read_text(encoding='utf-8').splitlines()
            if l.strip()]
    seen, groups = set(), []
    for r in rows:
        key = (r.get('goal'),
               (r.get('page') or '').rstrip('/').rsplit('/', 1)[-1])
        if key in seen:
            continue
        seen.add(key)
        groups.append(r)
    out, quar = [], []
    for r in groups:
        key = (r.get('goal'),
               (r.get('page') or '').rstrip('/').rsplit('/', 1)[-1])
        dec = DECISIONS.get(key)
        assert dec is not None, f'未登记决策: {key}'
        if r['goal'] in QUAR:      # 泄漏闸命中 → 隔离登记，不入主文件
            quar.append({'page': r.get('page'), 'query': r['goal'],
                         'reason': QUAR[r['goal']]})
            continue
        ref, negs, note = dec
        if ref is None:
            r['teacher_note'] = note
            out.append(r)          # chosen_ref 留空 → promote 拒收
            continue
        r['chosen_ref'] = ref
        r['negatives'] = negs
        r['teacher_note'] = note
        out.append(r)
    qpath = Path(__file__).parent / 'realmachine_quarantine.jsonl'
    qpath.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n'
                             for r in quar), encoding='utf-8')
    OUT.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n'
                           for r in out), encoding='utf-8')
    print(f'标注 {len(out)} 组（含 SKIP '
          f'{sum(1 for r in out if r["chosen_ref"] is None)} 行）→ {OUT}；'
          f'隔离 {len(quar)} 行 → {qpath}')

    # ── 泄漏三闸 ──
    labeled = [r for r in out if r['chosen_ref'] is not None]
    g1 = [(r['query'], g) for r in labeled for g in EVAL_GOALS
          if _hit(r['query'], {g})]
    print(f'闸1 query×eval-goal 命中: {len(g1)} {g1[:5]}')
    g2m = [(r['query'], t) for r in labeled
           for t in ([c for c in r.get('candidates', [])] + r['negatives'])
           if t in EVAL_OPTS_MISS]
    print(f'闸2a 候选串×miss-eval 复用: {len(g2m)} {g2m[:5]}')
    g2a = [(r['query'], t) for r in labeled
           for t in ([c for c in r.get('candidates', [])] + r['negatives'])
           if t in EVAL_OPTS_ALL]
    print(f'闸2b 候选串×全部eval(审计): {len(g2a)} {g2a[:8]}')
    qs = [r['query'] for r in labeled]
    g3 = ([q for q in set(qs) if qs.count(q) > 1],
          sorted(q for q in qs if q in V24_QUERIES))
    print(f'闸3 组内query重复: {g3[0]}；与v24 query 重复: {g3[1]}')


if __name__ == '__main__':
    main()
