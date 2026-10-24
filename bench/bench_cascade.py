#!/usr/bin/env python3
"""bge-reranker × Julia-1 级联 benchmark（实验产物，非主线）。

形态 A 前置筛：reranker top-3 元素预筛 → Julia 在收敛集终判。
形态 B advisory：reranker conf≥θ 直采，否则落 Julia 全量。
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bench_needle import CASES, _ROLE_ACTION
from bench_engines import candidates, score_row

JULIA_DIR = '/media/yan/Data/julia1-full'  # 拼装：上游 tokenizer 空占位+mmBERT 补齐
OUT = Path(__file__).parent / 'cascade_results.json'


def julia_pred(engine, case, opts):
    """opts=[(action, ref, text)] → Julia 在 opts 内选 index。"""
    state = json.dumps({'url': case['url'], 'title': case['title']},
                       ensure_ascii=False)
    rows = [{'state': state, 'question': case['goal'],
             'options': [t for _, _, t in opts]}]
    r = engine.predict(rows=rows)[0]
    a, ref, _ = opts[r['index']]
    return {'action': a, 'ref': ref,
            'confidence': max(r.get('probabilities') or [0])}


def main():
    from sentence_transformers import CrossEncoder
    import sys as _s
    _s.path.insert(0, JULIA_DIR)
    from julia.inference import load_model
    jm = load_model(JULIA_DIR, device='cpu')
    rk = CrossEncoder('BAAI/bge-reranker-base', max_length=512)

    rows = []
    for c in CASES:
        cands = candidates(c)
        # Julia options 上限 20：>20 时动作伪选项优先保留尾部截断元素
        if len(cands) > 20:
            acts = [x for x in cands if x[1] is None]
            els = [x for x in cands if x[1] is not None][:20 - len(acts)]
            cands = els + acts
        t0 = time.time()
        # reranker 全候选排序
        pairs = [[c['goal'], t] for _, _, t in cands]
        sims = rk.predict(pairs).tolist()
        scored = sorted(zip(cands, sims), key=lambda x: -x[1])
        rk_top = scored[0]
        rk_pred = {'action': rk_top[0][0], 'ref': rk_top[0][1],
                   'confidence': float(rk_top[1]),
                   'top3': [x[0][:2] for x in scored[:3]]}
        # top-3 候选集（元素+动作混合——reranker 筛什么 Julia 判什么）
        top3e = [x[0] for x in scored[:3]]
        if len(top3e) < 2:
            top3e = [x[0] for x in scored[:2]]
        t_rk = (time.time() - t0) * 1000

        t0 = time.time()
        jb = julia_pred(jm, c, cands)          # Julia 全量基线
        jc = julia_pred(jm, c, top3e)          # A：top-3 预筛后终判
        t_ju = (time.time() - t0) * 1000

        rows.append({'id': c['id'], 'expect': c['expect'],
                     'rk': rk_pred, 'julia_base': jb, 'julia_casc': jc,
                     'lat': {'rk': round(t_rk), 'julia': round(t_ju)}})
        print(f"{c['id']}: exp={c['expect']} rk={rk_pred['action']}:"
              f"{rk_pred['ref']}({rk_pred['confidence']:.2f}) "
              f"jb={jb['action']}:{jb['ref']} jc={jc['action']}:{jc['ref']}")

    def acc(key):
        return sum(score_row(r['expect'] and {'expect': r['expect']},
                             r[key])[0]
                   for r in rows)

    # 直接用 score_row 对 expect 判分
    def acc2(field):
        hits = sum(score_row(c, r[field])[0]
                   for c, r in zip(CASES, rows))
        return hits / len(rows)

    ths = {}
    for th in (0.5, 0.7, 0.8, 0.9, 0.95):
        h = 0
        for c, r in zip(CASES, rows):
            p = r['rk'] if r['rk']['confidence'] >= th else r['julia_base']
            h += score_row(c, p)[0]
        ths[th] = round(h / len(rows), 4)

    rep = {'n_cases': len(rows),
           'julia_alone': round(acc2('julia_base'), 4),
           'reranker_alone': round(acc2('rk'), 4),
           'cascade_top3_prefilter': round(acc2('julia_casc'), 4),
           'advisory_by_theta': ths,
           'latency_ms': {
               'rk_p50': sorted(r['lat']['rk'] for r in rows)[len(rows)//2],
               'julia_p50': sorted(r['lat']['julia'] for r in rows)[len(rows)//2]},
           'rows': rows}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    print('\n=== SUMMARY ===')
    print(f"julia_alone={rep['julia_alone']:.1%} rk_alone={rep['reranker_alone']:.1%} "
          f"casc_top3={rep['cascade_top3_prefilter']:.1%} advθ={ths}")
    print('→', OUT)


if __name__ == '__main__':
    main()
