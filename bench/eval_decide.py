#!/usr/bin/env python3
"""评测微调后 decide 模型——同 42 用例，对照 zero-shot 基线。"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bench_engines import candidates, score_row
from bench_needle import CASES

MODEL = sys.argv[1] if len(sys.argv) > 1 else \
    str(Path.home() / '.dp_cli/decide_model')
BASELINE = {'reranker_zero': 0.786, 'julia': 0.60}
GATE = 0.85


def main():
    from sentence_transformers import CrossEncoder
    m = CrossEncoder(MODEL, max_length=512)
    rows = []
    for c in CASES:
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
        full, act, ref, topk = score_row(c, pred)
        rows.append({'id': c['id'], 'hit': full, 'topk': topk,
                     'conf': pred['confidence'], 'lat': round(lat)})
    n = len(rows)
    acc = sum(r['hit'] for r in rows) / n
    tk = sum(r['topk'] for r in rows) / n
    ok = [r['conf'] for r in rows if r['hit']]
    bad = [r['conf'] for r in rows if not r['hit']]
    lats = sorted(r['lat'] for r in rows)
    gate = 'AUTONOMOUS' if acc >= GATE else 'ADVISORY'
    print(f'model={MODEL}')
    print(f'full={acc:.1%} topk={tk:.1%} p50={lats[n//2]}ms')
    print(f'conf: hit={sum(ok)/max(1,len(ok)):.3f} miss={sum(bad)/max(1,len(bad)):.3f}')
    print(f'baseline: reranker_zero={BASELINE["reranker_zero"]:.1%} '
          f'julia={BASELINE["julia"]:.0%}')
    print(f'GATE {GATE:.0%} → {gate}')


if __name__ == '__main__':
    main()
