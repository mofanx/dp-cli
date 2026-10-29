#!/usr/bin/env python3
"""评测 decide 模型——纪律：eval 集与训练种子来源级隔离。

eval = bench/eval_cases.py（原 holdout 8 + 新造 30 disjoint 样本，
       永不进 seed_train*）。n=38 单个 case ≈ ±2.6pp（vs 旧 n=8 ±12.5pp）。
分桶：element-pick（ref 非空）vs meta-action（done/wait/blocked/scroll/back）。
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bench_engines import candidates, score_row, rich_name
from eval_cases import EVAL_CASES

MODEL = sys.argv[1] if len(sys.argv) > 1 else \
    str(Path.home() / '.dp_cli/decide_model')
# argv[2]：margin 阈值扫档（逗号分隔，如 0.05,0.08,0.10,0.15）——
# 模拟生产 decide 的 margin 门：reranker 只出 target probs →
# margin_source='single_side' → margin=softmax p1-p2；UNSURE 计 miss。
MARGINS = ([float(x) for x in sys.argv[2].split(',')]
           if len(sys.argv) > 2 else [])
BASELINE = {'reranker_zero_42': 0.786, 'julia_42': 0.60}
GATE = 0.85
_META = {'scroll_down', 'scroll_up', 'back', 'wait', 'done', 'blocked'}


def _margin(sims):
    """生产同款：softmax(CE scores) → top1-top2（reranker=单边 target）。"""
    import math
    if not sims:
        return 1.0
    mx = max(sims)
    ps = sorted((math.exp(float(s) - mx) for s in sims), reverse=True)
    tot = sum(ps)
    p1 = ps[0] / tot
    p2 = ps[1] / tot if len(ps) > 1 else 0.0
    return p1 - p2


def main():
    from sentence_transformers import CrossEncoder
    m = CrossEncoder(MODEL, max_length=512)
    rows = []
    from dp_cli.agent import _meta_decide, _first_step
    for c in EVAL_CASES:
        # 系统级 eval——生产 decide 先走 _meta_decide 短路，eval 同步对齐
        meta = _meta_decide({'options': [{**o, 'name': rich_name(o)}
                                         for o in c['options']],
                             'state': {'title': c['title'], 'url': c['url']},
                             'epoch': 'x', 'truncated': False}, c['goal'])
        if meta:
            pred = {'action': meta['action'], 'ref': meta.get('ref'),
                    'confidence': 1.0, 'top3': [(meta['action'], meta.get('ref'))]}
            full, _, _, topk = score_row(c, pred)
            rows.append({'id': c['id'], 'bucket': 'meta', 'hit': full,
                         'topk': topk, 'conf': 1.0, 'lat': 0,
                         'margin': None})     # meta 短路在 margin 门之前
            continue
        cands = candidates(c)
        pairs = [[_first_step(c['goal']), t] for _, _, t in cands]
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
                     'lat': round(lat), 'margin': _margin(sims)})

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
    for thr in MARGINS:
        gated = [dict(r, unsure=(r['margin'] is not None
                                 and r['margin'] < thr))
                 for r in rows]
        un = [r for r in gated if r['unsure']]
        gh = lambda rs: (sum(1 for r in rs
                             if r['hit'] and not r['unsure'])
                         / max(1, len(rs)))
        fe = [r['id'] for r in un if r['hit']]      # 假拒（top1 对仍拒）
        te = [r['id'] for r in un if not r['hit']]  # 真拦（top1 错拒对）
        print(f'margin>={thr}: UNSURE={len(un)} '
              f'(假拒 {len(fe)} {fe} / 真拦 {len(te)} {te}) '
              f'ALL={gh(gated):.1%} '
              f'elem={gh([r for r in gated if r["bucket"]=="elem"]):.1%}')


if __name__ == '__main__':
    main()
