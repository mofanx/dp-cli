#!/usr/bin/env python3
"""xLAM-2-1b-fc-r decide benchmark——与 needle/ollama 同一用例集/同一指标。

adapter 形态 = v3 每元素一工具（needle bench 验证的唯一有效形态），
经 HF chat template 喂 tools——xLAM-2 是 FC 专训模型，原生吃 tool schema。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench_needle import CASES, make_tools, build_query, _ROLE_ACTION, \
    _ACT_MAP, score  # noqa: E402

OUT = Path(__file__).parent / 'xlam_results.json'
MODEL = 'Salesforce/xLAM-2-1b-fc-r'


def decode(name, role_by_ref):
    if name in _ACT_MAP:
        return _ACT_MAP[name], None
    if name.startswith('element_'):
        try:
            ref = int(name.split('_', 1)[1])
        except ValueError:
            return None, None
        return _ROLE_ACTION.get(role_by_ref.get(ref, ''), 'click'), ref
    return None, None


def main():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL)
    m = AutoModelForCausalLM.from_pretrained(MODEL, dtype='auto')
    rows = []
    for c in CASES:
        t0 = time.time()
        try:
            msgs = [{'role': 'user', 'content': build_query(c)}]
            ids = tok.apply_chat_template(
                msgs, tools=make_tools(c), add_generation_prompt=True,
                return_tensors='pt', return_dict=True)
            out = m.generate(**ids, max_new_tokens=128, do_sample=False)
            text = tok.decode(
                out[0][ids['input_ids'].shape[1]:], skip_special_tokens=True)
            calls = json.loads(text[text.index('['):text.rindex(']') + 1])
            names = [cl.get('name') for cl in calls if isinstance(cl, dict)]
            role_by_ref = {o['ref']: o['role'] for o in c['options']}
            action, ref = (decode(names[0], role_by_ref)
                           if names else (None, None))
            cands = sorted({str(decode(nm, role_by_ref)) for nm in names})
        except Exception as e:
            action, ref, cands = 'ERR', None, [str(e)[:80]]
        lat = (time.time() - t0) * 1000
        full, act, refh, topk = score(
            c, {'action': action, 'ref': ref, 'candidates': cands})
        rows.append({'id': c['id'], 'expect': c['expect'],
                     'pred': {'action': action, 'ref': ref},
                     'candidates': cands,
                     'hit_full': full, 'hit_action': act, 'hit_ref': refh,
                     'topk': topk, 'latency_ms': round(lat)})
        print(f"{c['id']}: exp={c['expect']} pred={action}:{ref} "
              f"{'OK' if full else 'MISS'}", flush=True)
    n = len(rows)
    lats = sorted(r['latency_ms'] for r in rows)
    rep = {'engine': MODEL, 'n_cases': n,
           'accuracy_full': round(sum(r['hit_full'] for r in rows) / n, 4),
           'accuracy_action': round(sum(r['hit_action'] for r in rows) / n, 4),
           'accuracy_ref': round(sum(r['hit_ref'] for r in rows) / n, 4),
           'topk_recall': round(sum(r['topk'] for r in rows) / n, 4),
           'latency_ms': {'p50': lats[n // 2],
                          'p95': lats[int(n * 0.95)]},
           'rows': rows}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"\n=== xLAM-2-1b-fc-r: full={rep['accuracy_full']:.1%} "
          f"topk={rep['topk_recall']:.1%} p50={lats[n//2]}ms ===")


if __name__ == '__main__':
    main()
