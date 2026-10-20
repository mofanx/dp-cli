#!/usr/bin/env python3
"""agent 离线 benchmark——Julia-1 vs LLM 基线双跑，分桶报告。

用法:
  python scripts/agent_bench.py --cases bench/cases.jsonl \
      --julia-cmd ~/bin/julia1 --llm-cmd <llm cli>

cases.jsonl 每行: {"state":{url,title},"options":[{ref,role,name}],
                   "expect":{"action":"click","ref":3}}
判定: Julia-1 相对准确率 ≥85% LLM 且延迟 <½ → 立项;否则只留控件表。
分桶: density=选项数(≤10/11-20/>20 截断), openness=has expected?
"""
import json, subprocess, sys, time, statistics
from pathlib import Path


def _bench_one(cmd, req, timeout=60):
    t = time.monotonic()
    try:
        p = subprocess.run(cmd, input=json.dumps(req) + '\n',
                           capture_output=True, text=True,
                           timeout=timeout)
        dt = time.monotonic() - t
        if p.returncode != 0:
            return None, dt
        return json.loads(p.stdout.strip().split('\n')[-1]), dt
    except Exception:
        return None, time.monotonic() - t


def _mk_req(case):
    return {'state': f"URL: {case['state']['url']}",
            'questions': {
                'action': {'type': 'choice',
                           'instructions': 'next action?',
                           'criteria': {a: a for a in
                                        ['click', 'type', 'select',
                                         'wait', 'done']}},
                'target': {'type': 'choice',
                           'instructions': 'which element?',
                           'criteria': {
                               str(o['ref']): o['name']
                               for o in case['options']}}}}


def _grade(resp, expect):
    if not resp or not expect:
        return False
    return (str(resp.get('action', '')).lower() ==
            expect['action'].lower()
            and int(resp.get('target', -1)) == expect['ref'])


def _bucket(case):
    n = len(case['options'])
    d = 'sparse' if n <= 10 else ('dense' if n <= 20 else 'truncated')
    o = 'open' if case.get('expect') else 'closed'
    return f'{d}/{o}'


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', required=True)
    ap.add_argument('--julia-cmd', nargs='+')
    ap.add_argument('--llm-cmd', nargs='+')
    ap.add_argument('-o', '--output')
    a = ap.parse_args()

    cases = [json.loads(l) for l in
             Path(a.cases).read_text().splitlines() if l.strip()]
    rows = {}
    for c in cases:
        b = _bucket(c)
        rows.setdefault(b, {'julia': [], 'llm': [],
                            'julia_t': [], 'llm_t': []})
        req = _mk_req(c)
        if a.julia_cmd:
            r, dt = _bench_one(a.julia_cmd, req)
            rows[b]['julia'].append(_grade(r, c.get('expect')))
            rows[b]['julia_t'].append(dt)
        if a.llm_cmd:
            r, dt = _bench_one(a.llm_cmd, req)
            rows[b]['llm'].append(_grade(r, c.get('expect')))
            rows[b]['llm_t'].append(dt)

    rep = {'buckets': {}, 'verdict': {}}
    for b, d in rows.items():
        entry = {'n': len(d['julia']) or len(d['llm'])}
        for eng in ('julia', 'llm'):
            if d[eng]:
                entry[f'{eng}_acc'] = round(
                    sum(d[eng]) / len(d[eng]), 3)
                entry[f'{eng}_ms_p50'] = round(
                    statistics.median(d[f'{eng}_t']) * 1000)
        rep['buckets'][b] = entry

    j_acc = [e['julia_acc'] for e in rep['buckets'].values()
             if 'julia_acc' in e]
    l_acc = [e['llm_acc'] for e in rep['buckets'].values()
             if 'llm_acc' in e]
    j_t = [e['julia_ms_p50'] for e in rep['buckets'].values()
           if 'julia_ms_p50' in e]
    l_t = [e['llm_ms_p50'] for e in rep['buckets'].values()
           if 'llm_ms_p50' in e]
    if j_acc and l_acc:
        rel = statistics.mean(j_acc) / max(statistics.mean(l_acc), 1e-9)
        spd = statistics.mean(j_t) / max(statistics.mean(l_t), 1e-9)
        rep['verdict'] = {
            'relative_acc': round(rel, 3), 'latency_ratio': round(spd, 3),
            'go': rel >= 0.85 and spd < 0.5,
            'note': 'go=True → decide 立项; False → 只留 agent-state'}
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if a.output:
        Path(a.output).write_text(
            json.dumps(rep, ensure_ascii=False, indent=2),
            encoding='utf-8')


if __name__ == '__main__':
    main()
