#!/usr/bin/env python3
"""v24 BGE reranker ONNX+int8 POC——三对照：延迟/体积内存/精度。

用法：python bench/onnx_poc.py [onnx_dir]
  onnx_dir 默认 /media/yan/Data/dpx_onnx_poc（需含 fp32/model.onnx 与
  model_int8.onnx）。结果写 bench/onnx_poc_report.json。
评测对齐 eval_decide：meta 短路 + _first_step(goal) + candidates() 候选集 +
score_row 判分。ORT 路径与生产 _engine_reranker 一致：max_length=128、
仅喂 input_ids/attention_mask（XLMRoberta tokenizer 不出 token_type_ids）。
"""
import gc, json, math, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bench'))
sys.path.insert(0, str(ROOT))

from bench_engines import candidates, score_row, rich_name   # noqa: E402
from eval_cases import EVAL_CASES                             # noqa: E402

BGE = Path.home() / '.dp_cli/bge_model'
ONNX_DIR = Path(sys.argv[1] if len(sys.argv) > 1
                else '/media/yan/Data/dpx_onnx_poc')
FP32_ONNX = ONNX_DIR / 'fp32' / 'model.onnx'
INT8_ONNX = ONNX_DIR / 'model_int8.onnx'
ORT_MAXLEN = 128            # 对齐生产 _engine_reranker


def rss_mb():
    for line in open('/proc/self/status'):
        if line.startswith('VmRSS'):
            return int(line.split()[1]) / 1024
    return -1


def build_cases():
    """eval_decide 同款：meta 短路标注 + 每案 (goal首步, cands)。"""
    from dp_cli.agent import _meta_decide, _first_step
    built = []
    for c in EVAL_CASES:
        meta = _meta_decide({'options': [{**o, 'name': rich_name(o)}
                                         for o in c['options']],
                             'state': {'title': c['title'], 'url': c['url']},
                             'epoch': 'x', 'truncated': False}, c['goal'])
        cands = candidates(c)
        pairs = [[_first_step(c['goal']), t] for _, _, t in cands]
        built.append({'case': c, 'meta': meta, 'cands': cands, 'pairs': pairs})
    return built


def flat_pairs(built):
    return [p for b in built for p in b['pairs']]


def eval_hits(built, score_batch):
    """score_batch(pairs)->list[float]；meta 案直接记 hit=1（同 eval_decide）。"""
    rows, all_scores = [], []
    for b in built:
        if b['meta']:
            pred = {'action': b['meta']['action'], 'ref': b['meta'].get('ref'),
                    'top3': [(b['meta']['action'], b['meta'].get('ref'))]}
            full, _, _, topk = score_row(b['case'], pred)
            rows.append(full)
            all_scores.append(None)
            continue
        sims = [float(s) for s in score_batch(b['pairs'])]
        scored = sorted(((a, r, s) for (a, r, _), s in zip(b['cands'], sims)),
                        key=lambda x: -x[2])
        top = scored[0]
        pred = {'action': top[0], 'ref': top[1],
                'top3': [(a, r) for a, r, _ in scored[:3]]}
        full, _, _, topk = score_row(b['case'], pred)
        rows.append(full)
        all_scores.append(sims)
    return rows, all_scores


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


def main():
    rep = {'model': str(BGE), 'onnx_dir': str(ONNX_DIR),
           'n_cases': len(EVAL_CASES), 'engines': {}, 'accuracy': {},
           'files': {}}
    for p, k in [(BGE / 'model.safetensors', 'safetensors'),
                 (FP32_ONNX, 'onnx_fp32'), (INT8_ONNX, 'onnx_int8')]:
        rep['files'][k] = round(p.stat().st_size / 1e6, 1) if p.exists() else None

    built = build_cases()
    pairs_all = flat_pairs(built)
    rep['n_pairs'] = len(pairs_all)
    probe = pairs_all[::max(1, len(pairs_all) // 50)][:50]   # 50 对单条延迟探针
    rep['rss'] = {'baseline_mb': round(rss_mb(), 1)}

    # ── 引擎 1：ST CrossEncoder fp32 ─────────────────────────
    from sentence_transformers import CrossEncoder
    st = CrossEncoder(str(BGE), max_length=512)
    rep['rss']['st_loaded_mb'] = round(rss_mb(), 1)

    def st_batch(ps):
        return st.predict(ps).tolist()

    for p in probe[:5]:
        st.predict([p])                       # warmup
    lat1 = []
    for p in probe:
        t0 = time.time()
        st.predict([p])
        lat1.append((time.time() - t0) * 1000)
    case_lat = []
    for b in built:
        if b['meta']:
            continue
        t0 = time.time()
        st.predict(b['pairs'])
        case_lat.append((time.time() - t0) * 1000)
    t0 = time.time()
    st.predict(pairs_all)                     # 全量吞吐
    thr = (time.time() - t0)
    rep['engines']['st_fp32'] = {
        'pair_p50_ms': round(median(lat1), 1),
        'case_p50_ms': round(median(case_lat), 1),
        'throughput_pairs_s': round(len(pairs_all) / thr, 1),
        'n_pairs': len(pairs_all)}
    st_hits, st_scores = eval_hits(built, st_batch)
    rep['accuracy']['st_fp32'] = {
        'full': round(sum(st_hits) / len(st_hits), 4)}
    del st
    gc.collect()
    rep['rss']['after_st_del_mb'] = round(rss_mb(), 1)

    # ── 引擎 2/3：ORT fp32 / int8 ────────────────────────────
    import onnxruntime as ort
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(str(BGE))

    def make_ort(path):
        sess = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])

        def batch(ps):
            enc = tok([p[0] for p in ps], [p[1] for p in ps],
                      return_tensors='np', padding=True, truncation=True,
                      max_length=ORT_MAXLEN)
            return sess.run(['logits'], {i.name: enc[i.name]
                                         for i in sess.get_inputs()}
                            )[0].ravel().tolist()
        return sess, batch

    ort_scores = {}
    for name, path in [('ort_fp32', FP32_ONNX), ('ort_int8', INT8_ONNX)]:
        rss0 = rss_mb()
        sess, batch = make_ort(path)
        rep['rss'][f'{name}_loaded_mb'] = round(rss_mb(), 1)
        rep['rss'][f'{name}_load_delta_mb'] = round(rss_mb() - rss0, 1)
        for p in probe[:5]:
            batch([p])
        lat1 = []
        for p in probe:
            t0 = time.time()
            batch([p])
            lat1.append((time.time() - t0) * 1000)
        case_lat = []
        for b in built:
            if b['meta']:
                continue
            t0 = time.time()
            batch(b['pairs'])
            case_lat.append((time.time() - t0) * 1000)
        t0 = time.time()
        bs = 256
        for i in range(0, len(pairs_all), bs):
            batch(pairs_all[i:i + bs])
        thr = time.time() - t0
        rep['engines'][name] = {
            'pair_p50_ms': round(median(lat1), 1),
            'case_p50_ms': round(median(case_lat), 1),
            'throughput_pairs_s': round(len(pairs_all) / thr, 1)}
        hits, scores = eval_hits(built, batch)
        rep['accuracy'][name] = {'full': round(sum(hits) / len(hits), 4)}
        ort_scores[name] = (hits, scores)
        del sess, batch
        gc.collect()
        rep['rss'][f'after_{name}_del_mb'] = round(rss_mb(), 1)

    # ── 精度对照：sigmoid 化对齐 ST 输出 + top1 翻转 ────────
    # ST predict 对单 logit 头套 sigmoid 返回 [0,1]；ORT 出 raw logits——
    # 必须 sigmoid 化再比分差；排序两者等价（单调变换）。
    import statistics

    def sig(x):
        return 1 / (1 + math.exp(-max(-30, min(30, x))))

    diffs8, diffsf, raw8, flips_fp32, flips_int8 = [], [], [], 0, 0
    for i, b in enumerate(built):
        if b['meta']:
            continue
        s_st = st_scores[i]
        s_f = ort_scores['ort_fp32'][1][i]
        s_8 = ort_scores['ort_int8'][1][i]
        if max(range(len(s_f)), key=lambda j: s_f[j]) != \
           max(range(len(s_st)), key=lambda j: s_st[j]):
            flips_fp32 += 1
        if max(range(len(s_8)), key=lambda j: s_8[j]) != \
           max(range(len(s_st)), key=lambda j: s_st[j]):
            flips_int8 += 1
        diffsf += [abs(a - sig(c)) for a, c in zip(s_st, s_f)]
        diffs8 += [abs(a - sig(c)) for a, c in zip(s_st, s_8)]
        raw8 += [abs(a - c) for a, c in zip(s_f, s_8)]
    n_sc = sum(1 for b in built if not b['meta'])
    rep['accuracy']['score_diff'] = {
        'note': 'ST predict=sigmoid 概率；ORT=raw logits（sigmoid 化对比）',
        'ort_fp32_vs_st_sigmoid_absdiff_mean': round(statistics.fmean(diffsf), 5),
        'ort_fp32_vs_st_sigmoid_absdiff_max': round(max(diffsf), 5),
        'int8_vs_st_sigmoid_absdiff_mean': round(statistics.fmean(diffs8), 5),
        'int8_vs_st_sigmoid_absdiff_max': round(max(diffs8), 5),
        'int8_vs_fp32_rawlogit_absdiff_mean': round(statistics.fmean(raw8), 4),
        'int8_vs_fp32_rawlogit_absdiff_max': round(max(raw8), 4),
        'top1_flip_cases_int8': f'{flips_int8}/{n_sc}',
        'top1_flip_cases_ort_fp32': f'{flips_fp32}/{n_sc}'}

    # ── RSS 精测：每引擎独立子进程量 load delta ──────────────
    import subprocess
    for name, path in [('ort_fp32', FP32_ONNX), ('ort_int8', INT8_ONNX)]:
        code = (
            "import onnxruntime as o\n"
            "def r():\n"
            "    return int([l.split()[1] for l in open('/proc/self/status')\n"
            "                if l.startswith('VmRSS')][0])/1024\n"
            "b=r(); o.InferenceSession(sys.argv[1])\n"
            "print(round(r()-b,1))")
        d = subprocess.run([sys.executable, '-c', 'import sys\n' + code,
                            str(path)], capture_output=True, text=True)
        rep['rss'][f'{name}_subproc_load_delta_mb'] = \
            float(d.stdout.strip()) if d.returncode == 0 else d.stderr[-120:]
    code_st = (
        "import sys\n"
        "from sentence_transformers import CrossEncoder\n"
        "def r():\n"
        "    return int([l.split()[1] for l in open('/proc/self/status')\n"
        "                if l.startswith('VmRSS')][0])/1024\n"
        "b=r(); CrossEncoder(sys.argv[1], max_length=512)\n"
        "print(round(r()-b,1))")
    d = subprocess.run([sys.executable, '-c', code_st, str(BGE)],
                       capture_output=True, text=True)
    rep['rss']['st_subproc_load_delta_mb'] = \
        float(d.stdout.strip()) if d.returncode == 0 else d.stderr[-120:]

    out = ROOT / 'bench' / 'onnx_poc_report.json'
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
