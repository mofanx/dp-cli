#!/usr/bin/env python3
"""Julia-1 归因探针——一次载模跑 bench cases，逐条失败明细+格式消融。

用法:
  python scripts/julia_probe.py --cases bench/cases.jsonl [--fmt base|rich-state|rich-label|all]

base        当前 _mk_req 格式（state=URL: ...，label=name）
rich-state  state 加 title+options 上下文
rich-label  label 加 role 前缀（"button Sign in"）
"""
import json, sys, time
from pathlib import Path

_HF = Path.home() / '.cache/huggingface/hub/models--SupersonicLabs--Julia-1/snapshots'
SNAP = sorted(_HF.glob('*/'))[-1]
sys.path.insert(0, str(SNAP))
from julia import load_model          # noqa: E402
from julia.typed import predict_typed  # noqa: E402


def _mk(case, fmt):
    """构造 predict_typed 请求——消融点：state 内容与 label 粒度。"""
    s = case['state']
    goal = case.get('goal', '')
    if fmt == 'rich-state':
        names = ' | '.join(o['name'] for o in case['options'][:10])
        state = f"URL: {s['url']}\nTitle: {s.get('title','')}\nVisible controls: {names}"
    elif fmt == 'goal':
        state = (f"Page: {s.get('title','')} ({s['url']})\n"
                 f"Goal: {goal}")
    else:
        state = f"URL: {s['url']}"
    label = (lambda o: f"{o['role']} {o['name']}" if fmt == 'rich-label'
             else o['name'])
    if fmt == 'goal3':  # target+done-noul：action 由 role 代码映射
        return {'state': f"Page: {s.get('title','')} ({s['url']})\nGoal: {goal}",
                'questions': {
                    'done': {'type': 'noul',
                             'instructions': 'Is the goal already complete '
                                            'on this page?'},
                    'target': {'type': 'choice',
                               'instructions': f"Goal: {goal}. "
                                               'Which element advances this goal?',
                               'criteria': {str(o['ref']): o['name']
                                            for o in case['options']}}}}
    ins_t = (f"Goal: {goal}. Which element advances this goal?"
             if fmt in ('goal', 'goal2') else 'which element?')
    ins_a = (f"Goal: {goal}. What is the next action?"
             if fmt in ('goal', 'goal2') else 'next action?')
    if fmt == 'goal2':
        act_crit = {'click': 'click or press the element',
                    'type': 'type text into an input field',
                    'select': 'pick an option from a dropdown',
                    'wait': 'wait for the page to load',
                    'done': 'goal already complete, stop'}
    else:
        act_crit = {a: a for a in
                    ['click', 'type', 'select', 'wait', 'done']}
    return {'state': state,
            'questions': {
                'action': {'type': 'choice',
                           'instructions': ins_a,
                           'criteria': act_crit},
                'target': {'type': 'choice',
                           'instructions': ins_t,
                           'criteria': {str(o['ref']): label(o)
                                        for o in case['options']}}}}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', required=True)
    ap.add_argument('--fmt', default='all')
    a = ap.parse_args()
    cases = [json.loads(l) for l in Path(a.cases).read_text().splitlines()
             if l.strip()]
    engine = load_model(str(SNAP), device='cpu')
    fmts = ([a.fmt] if a.fmt != 'all'
            else ['base', 'goal', 'goal3'])
    out = {}
    for fmt in fmts:
        rows, dts = [], []
        for c in cases:
            exp = c.get('expect') or {}
            t = time.monotonic()
            try:
                r = predict_typed(engine, **_mk(c, fmt))
                ans = r['answers']
                got_t = int(ans['target'].get('choice', -1))
                probs = ans['target'].get('probabilities', {})
                if fmt == 'goal3':  # action 代码映射：done-noul + role→动作
                    done_p = (ans['done'].get('probabilities', {})
                              .get('true', 0))
                    if done_p >= 0.5:
                        got_a, got_t = 'done', exp.get('ref', -1)
                    else:
                        role = next((o['role'] for o in c['options']
                                     if o['ref'] == got_t), '')
                        got_a = ('type' if role in
                                 ('textbox', 'combobox', 'searchbox')
                                 else 'select' if role == 'listbox'
                                 else 'click')
                else:
                    got_a = ans['action'].get('choice')
            except Exception as e:  # noqa: BLE001
                got_a, got_t, probs = f'ERR:{e}', -1, {}
            dts.append(time.monotonic() - t)
            ok = (str(got_a or '').lower() == exp.get('action', '').lower()
                  and got_t == exp.get('ref', -2))
            rows.append({'ok': ok, 'expect': exp, 'got': got_t,
                         'got_action': got_a,
                         'top_p': round(max(probs.values() or [0]), 3)})
        n_ok = sum(r['ok'] for r in rows)
        import statistics
        out[fmt] = {'acc': round(n_ok / len(rows), 3),
                    'n': len(rows), 'ms_p50': round(
                        statistics.median(dts) * 1000),
                    'fails': [r for r in rows if not r['ok']]}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
