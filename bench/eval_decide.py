#!/usr/bin/env python3
"""评测 decide 模型——纪律：eval 集与训练种子来源级隔离。

eval = bench/eval_cases.py（原 holdout 8 + 新造 30 disjoint 样本，
       永不进 seed_train*）。n=38 单个 case ≈ ±2.6pp（vs 旧 n=8 ±12.5pp）。
分桶：element-pick（ref 非空）vs meta-action（done/wait/blocked/scroll/back）。
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bench_engines import candidates, score_row
from eval_cases import EVAL_CASES

MODEL = sys.argv[1] if len(sys.argv) > 1 else \
    str(Path.home() / '.dp_cli/decide_model')
BASELINE = {'reranker_zero_42': 0.786, 'julia_42': 0.60}
GATE = 0.85
_META = {'scroll_down', 'scroll_up', 'back', 'wait', 'done', 'blocked'}


def main():
    from sentence_transformers import CrossEncoder
    m = CrossEncoder(MODEL, max_length=512)
    rows = []
    from dp_cli.agent import _meta_decide
    for c in EVAL_CASES:
        # 系统级 eval——生产 decide 先走 _meta_decide 短路，eval 同步对齐
        meta = _meta_decide({'options': c['options'],
                             'state': {'title': c['title'], 'url': c['url']},
                             'epoch': 'x', 'truncated': False}, c['goal'])
        if meta:
            pred = {'action': meta['action'], 'ref': None,
                    'confidence': 1.0, 'top3': [(meta['action'], None)]}
            full, _, _, topk = score_row(c, pred)
            rows.append({'id': c['id'], 'bucket': 'meta', 'hit': full,
                         'topk': topk, 'conf': 1.0, 'lat': 0})
            continue
        cands = candidates(c)
        pairs = [[c['goal'], t] for _, _, t in cands]
        t0 = time.time()
        sims = m.predict(pairs).tolist()
        lat = (time.time() - t0) * 1000
        scored = sorted(((a, r, float(s))
                         for (a, r, _), s in zip(cands, sims)),
                        key=lambda x: -x[2])
        top = scored[0]
        pred = {'action': top[0], 'ref': top[1], 'confidence': top[2],
                'top3': [(a, r) for a, r, _ in scored[:3]]}
        full, _, _, topk = score_row(c, pred)
        bucket = 'meta' if c['expect']['action'] in _META else 'elem'
        rows.append({'id': c['id'], 'bucket': bucket, 'hit': full,
                     'topk': topk, 'conf': pred['confidence'],
                     'lat': round(lat)})

    def agg(rs, label):
        n = len(rs)
        if not n:
            return f'{label}: n=0'
        return (f'{label}: full={sum(r["hit"] for r in rs)/n:.1%} '
                f'topk={sum(r["topk"] for r in rs)/n:.1%} (n={n})')

    print(f'model={MODEL}')
    print(agg(rows, 'ALL   '))
    print(agg([r for r in rows if r['bucket'] == 'elem'], 'elem  '))
    print(agg([r for r in rows if r['bucket'] == 'meta'], 'meta  '))
    ok = [r['conf'] for r in rows if r['hit']]
    bad = [r['conf'] for r in rows if not r['hit']]
    lats = sorted(r['lat'] for r in rows)
    print(f'conf: hit={sum(ok)/max(1,len(ok)):.3f} '
          f'miss={sum(bad)/max(1,len(bad)):.3f}  p50={lats[len(lats)//2]}ms')
    acc = sum(r['hit'] for r in rows) / len(rows)
    print(f'eval n={len(rows)}（disjoint，±{100/len(rows):.1f}pp/case）')
    print(f'GATE {GATE:.0%} → {"AUTONOMOUS" if acc >= GATE else "ADVISORY"}')
    miss = [r['id'] for r in rows if not r['hit']]
    print(f'miss: {miss}')


if __name__ == '__main__':
    main()
