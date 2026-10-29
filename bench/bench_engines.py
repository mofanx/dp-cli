#!/usr/bin/env python3
"""decide 引擎横向对比 benchmark（非主线代码）。

同一 41 用例（bench_needle.CASES）跑四个引擎：
  lex      rapidfuzz 词法匹配（零模型基线）
  e5       multilingual-e5-small 双编码器 cosine
  reranker bge-reranker-base 交叉编码器（zh+en）
  qwen06   Qwen3-0.6B 生成式 JSON（transformers CPU）

排名类引擎（lex/e5/reranker）：候选 = 元素 + 6 个动作伪选项，
goal↔候选相似度取 argmax；ref 命中后 action 由 _goal_action 映射
（生产 agent.py 同一函数——type 族角色无输入意图降级 click）。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench_needle import CASES  # noqa: E402

OUT = Path(__file__).parent / 'engines_results.json'

_ACT_CANDS = [
    ('scroll_down', 'scroll down to reveal more content below'),
    ('scroll_up', 'scroll up back to the top of the page'),
    ('back', 'go back to the previous page'),
    ('wait', 'wait for the page to finish loading'),
    ('done', 'the goal is already complete, no action needed'),
    ('blocked', 'progress is blocked by a captcha or verification wall'),
]


def rich_name(o):
    """生产候选富化形态：name [opt1|opt2]。

    opt_values 模拟 combobox options 注入（258ba0d，select 桶实测
    有效）。'[in 容器]' 祖先语义注入经 eval-120 否证（-1.7pp）已回滚
    ——o['parent'] 字段保留不消费。
    """
    name = o['name']
    if o.get('opt_values'):
        name = f"{name} [{'|'.join(o['opt_values'])}]"
    return name


def candidates(case):
    """统一候选表：元素 + 动作伪选项 → [(action, ref, text)]"""
    from dp_cli.agent import _goal_action      # eval 与生产同映射
    rows = []
    for o in case['options']:
        rows.append((_goal_action(case['goal'], o['role']), o['ref'],
                     f"{o['role']} {rich_name(o)}"))
    for a, t in _ACT_CANDS:
        rows.append((a, None, t))
    return rows


def run_ranker(case, score_fn):
    """score_fn(goal, cand_text) -> float；返回 pred + top3。"""
    cands = candidates(case)
    scored = [(a, r, score_fn(case['goal'], t))
              for a, r, t in cands]
    scored.sort(key=lambda x: -x[2])
    top = scored[0]
    top3 = [(a, r) for a, r, _ in scored[:3]]
    return {'action': top[0], 'ref': top[1], 'confidence': top[2],
            'top3': top3}


def score_row(case, pred):
    exp = case['expect']
    full = (pred['action'] == exp['action']
            and (exp['ref'] is None or pred['ref'] == exp['ref']))
    act = pred['action'] == exp['action']
    ref = (exp['ref'] is None) or (pred['ref'] == exp['ref'])
    topk = any(a == exp['action'] and (exp['ref'] is None or r == exp['ref'])
               for a, r in pred.get('top3', []))
    return full, act, ref, topk


# ── 引擎 ─────────────────────────────────────────────────────

def engine_lex():
    from rapidfuzz import fuzz

    def score(goal, text):
        return max(fuzz.partial_ratio(goal, text),
                   fuzz.token_set_ratio(goal, text)) / 100.0

    def pred(case):
        return run_ranker(case, score)
    return pred, {}


def engine_e5():
    from sentence_transformers import SentenceTransformer
    import torch
    m = SentenceTransformer('intfloat/multilingual-e5-small')
    texts = {id(c): [f"passage: {t}" for _, _, t in candidates(c)]
             for c in CASES}
    emb_cache = {cid: m.encode(ts, convert_to_tensor=True)
                 for cid, ts in texts.items()}

    def pred(case):
        q = m.encode(f"query: {case['goal']}", convert_to_tensor=True)
        doc = emb_cache[id(case)]
        sims = torch.cosine_similarity(q.unsqueeze(0), doc).tolist()
        cands = candidates(case)
        scored = sorted(((a, r, s) for (a, r, _), s in zip(cands, sims)),
                        key=lambda x: -x[2])
        top = scored[0]
        return {'action': top[0], 'ref': top[1], 'confidence': top[2],
                'top3': [(a, r) for a, r, _ in scored[:3]]}
    return pred, {'model': 'intfloat/multilingual-e5-small',
                  'size_mb': 470}


def engine_reranker():
    from sentence_transformers import CrossEncoder
    m = CrossEncoder('BAAI/bge-reranker-base', max_length=512)

    def pred(case):
        cands = candidates(case)
        pairs = [[case['goal'], t] for _, _, t in cands]
        sims = m.predict(pairs).tolist()
        scored = sorted(((a, r, float(s))
                         for (a, r, _), s in zip(cands, sims)),
                        key=lambda x: -x[2])
        top = scored[0]
        return {'action': top[0], 'ref': top[1], 'confidence': top[2],
                'top3': [(a, r) for a, r, _ in scored[:3]]}
    return pred, {'model': 'BAAI/bge-reranker-base',
                  'size_mb': 1100}


def make_hfjson(name, **gen_kw):
    """生成式基座通用 engine——JSON 输出 {action,ref}。"""
    def factory():
        from transformers import AutoModelForCausalLM, AutoTokenizer
        tok = AutoTokenizer.from_pretrained(name)
        m = AutoModelForCausalLM.from_pretrained(name, dtype='auto')
        acts = ('click,type,select,scroll_up,scroll_down,'
                'back,wait,done,blocked')

        def pred(case):
            lines = [f"Page title: {case['title']}", f"Goal: {case['goal']}",
                     'Elements:']
            for o in case['options']:
                lines.append(f"{o['ref']}. {o['role']}: {o['name']}")
            prompt = ('\n'.join(lines) +
                      f"\n\nReply with JSON only: "
                      f'{{"action": one of {acts}, "ref": element number or null}}')
            msgs = [{'role': 'user', 'content': prompt}]
            ids = tok.apply_chat_template(msgs, add_generation_prompt=True,
                                        return_tensors='pt', return_dict=True,
                                        enable_thinking=False)
            out = m.generate(**ids, max_new_tokens=64, do_sample=False,
                             **gen_kw)
            text = tok.decode(out[0][ids['input_ids'].shape[1]:],
                              skip_special_tokens=True)
            try:
                j = json.loads(text[text.index('{'):text.rindex('}') + 1])
                return {'action': j.get('action'), 'ref': j.get('ref'),
                        'confidence': None, 'top3': []}
            except Exception:
                return {'action': None, 'ref': None,
                        'confidence': None, 'top3': []}
        return pred, {'model': name}
    return factory


def engine_qwen06():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    name = 'Qwen/Qwen3-0.6B'
    tok = AutoTokenizer.from_pretrained(name)
    m = AutoModelForCausalLM.from_pretrained(name, dtype='auto')
    acts = ('click,type,select,scroll_up,scroll_down,'
            'back,wait,done,blocked')

    def pred(case):
        lines = [f"Page title: {case['title']}", f"Goal: {case['goal']}",
                 'Elements:']
        for o in case['options']:
            lines.append(f"{o['ref']}. {o['role']}: {o['name']}")
        prompt = ('\n'.join(lines) +
                  f"\n\nReply with JSON only: "
                  f'{{"action": one of {acts}, "ref": element number or null}}')
        msgs = [{'role': 'user', 'content': prompt}]
        ids = tok.apply_chat_template(msgs, add_generation_prompt=True,
                                    return_tensors='pt', return_dict=True,
                                    enable_thinking=False)
        out = m.generate(**ids, max_new_tokens=64, do_sample=False)
        text = tok.decode(out[0][ids['input_ids'].shape[1]:],
                          skip_special_tokens=True)
        try:
            j = json.loads(text[text.index('{'):text.rindex('}') + 1])
            return {'action': j.get('action'), 'ref': j.get('ref'),
                    'confidence': None, 'top3': []}
        except Exception:
            return {'action': None, 'ref': None,
                    'confidence': None, 'top3': []}
    return pred, {'model': 'Qwen/Qwen3-0.6B', 'size_mb': 1200}


ENGINES = {'lex': engine_lex, 'e5': engine_e5,
           'reranker': engine_reranker, 'qwen06': engine_qwen06,
           'lfm25': make_hfjson('LiquidAI/LFM2.5-350M'),
           'qwen25_05b': make_hfjson('Qwen/Qwen2.5-0.5B-Instruct'),
           'smollm2': make_hfjson('HuggingFaceTB/SmolLM2-360M-Instruct')}


def main():
    which = sys.argv[1:] or list(ENGINES)
    report = {'n_cases': len(CASES), 'engines': {}}
    for eng in which:
        print(f'\n=== {eng} ===')
        pred_fn, meta = ENGINES[eng]()
        rows = []
        for c in CASES:
            t0 = time.time()
            p = pred_fn(c)
            lat = (time.time() - t0) * 1000
            full, act, ref, topk = score_row(c, p)
            rows.append({'id': c['id'], 'expect': c['expect'],
                         'pred': {k: p[k] for k in ('action', 'ref')},
                         'conf': p['confidence'],
                         'hit_full': full, 'hit_action': act,
                         'hit_ref': ref, 'topk': topk,
                         'latency_ms': round(lat)})
            print(f"  {c['id']}: exp={c['expect']} "
                  f"pred={p['action']}:{p['ref']} "
                  f"conf={round(p['confidence'],3) if p['confidence'] is not None else '-'} "
                  f"{'OK' if full else 'MISS'}")
        n = len(rows)
        lats = sorted(r['latency_ms'] for r in rows)
        confs_ok = [r['conf'] for r in rows
                    if r['hit_full'] and r['conf'] is not None]
        confs_bad = [r['conf'] for r in rows
                     if not r['hit_full'] and r['conf'] is not None]
        report['engines'][eng] = {
            **meta,
            'accuracy_full': round(sum(r['hit_full'] for r in rows) / n, 4),
            'accuracy_action': round(sum(r['hit_action'] for r in rows) / n, 4),
            'accuracy_ref': round(sum(r['hit_ref'] for r in rows) / n, 4),
            'top3_recall': round(sum(r['topk'] for r in rows) / n, 4),
            'latency_ms': {'p50': lats[n // 2], 'p95': lats[int(n * 0.95)]},
            'conf_hit_mean': round(sum(confs_ok) / len(confs_ok), 4)
            if confs_ok else None,
            'conf_miss_mean': round(sum(confs_bad) / len(confs_bad), 4)
            if confs_bad else None,
            'rows': rows}
        print(f"  → full={report['engines'][eng]['accuracy_full']:.1%} "
              f"top3={report['engines'][eng]['top3_recall']:.1%} "
              f"p50={lats[n//2]}ms")
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('\nreport →', OUT)


if __name__ == '__main__':
    main()
