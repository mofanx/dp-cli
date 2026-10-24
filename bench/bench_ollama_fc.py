#!/usr/bin/env python3
"""Ollama function-calling decide benchmark——与 needle 同一用例集/同一 adapter/同一指标。"""
import json, time, urllib.request
import sys; sys.path.insert(0, 'bench')
from bench_needle import CASES, make_tools, build_query, _ROLE_ACTION, _ACT_MAP, score

OUT = 'bench/ollama_fc_results.json'
MODEL = sys.argv[1] if len(sys.argv) > 1 else 'qwen2.5:1.5b'

def ollama_call(query, tools):
    ollama_tools = [{'type': 'function', 'function': {
        'name': t['name'], 'description': t['description'],
        'parameters': t['parameters']}} for t in tools]
    body = {'model': MODEL, 'stream': False,
            'messages': [
                {'role': 'system', 'content':
                 'You are a browser automation agent. Call the element that '
                 'best advances the goal, or an action tool (act_*) when no '
                 'element fits. Use act_done when the goal is achieved.'},
                {'role': 'user', 'content': query}],
            'tools': ollama_tools,
            'options': {'temperature': 0}}
    req = urllib.request.Request('http://localhost:11434/api/chat',
        json.dumps(body).encode(), {'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())

def decode(name, role_by_ref):
    if name in _ACT_MAP:
        return _ACT_MAP[name], None
    if name.startswith('element_'):
        try: ref = int(name.split('_', 1)[1])
        except ValueError: return None, None
        return _ROLE_ACTION.get(role_by_ref.get(ref, ''), 'click'), ref
    return None, None

rows = []
for c in CASES:
    t0 = time.time()
    try:
        r = ollama_call(build_query(c), make_tools(c))
        calls = r.get('message', {}).get('tool_calls') or []
        names = [cl['function']['name'] for cl in calls]
        role_by_ref = {o['ref']: o['role'] for o in c['options']}
        action, ref = decode(names[0], role_by_ref) if names else (None, None)
        cands = sorted({str(decode(nm, role_by_ref)) for nm in names})
    except Exception as e:
        action, ref, cands = 'ERR', None, [str(e)[:60]]
    lat = (time.time() - t0) * 1000
    full, act, refh, topk = score(c, {'action': action, 'ref': ref, 'candidates': cands})
    rows.append({'id': c['id'], 'expect': c['expect'],
                 'pred': {'action': action, 'ref': ref}, 'candidates': cands,
                 'hit_full': full, 'topk': topk, 'latency_ms': round(lat)})
    print(f"{c['id']}: exp={c['expect']} pred={action}:{ref} {'OK' if full else 'MISS'}")

n = len(rows)
acc = sum(r['hit_full'] for r in rows)/n
topk_r = sum(r['topk'] for r in rows)/n
lats = sorted(r['latency_ms'] for r in rows)
rep = {'engine': f'ollama {MODEL}', 'n_cases': n, 'accuracy_full': round(acc, 4),
       'topk_recall': round(topk_r, 4),
       'latency_ms': {'p50': lats[n//2], 'p95': lats[int(n*0.95)]}, 'rows': rows}
json.dump(rep, open(OUT, 'w'), ensure_ascii=False, indent=2)
print(f'\nfull={acc:.1%} topk={topk_r:.1%} p50={lats[n//2]}ms → {OUT}')
