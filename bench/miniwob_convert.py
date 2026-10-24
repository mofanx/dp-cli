#!/usr/bin/env python3
"""MiniWoB++ HTML → 训练 pairs——goal=任务 utterance（自然语言真分布），
chosen=utterance 中命名的目标元素（元素名在 goal 里出现的才作正例）。"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from collect_sites import _P

MW = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/miniwob-plusplus/miniwob/html/miniwob')
OUT = Path(__file__).parent / 'miniwob_pairs.json'


def parse(path: Path):
    html = path.read_text(encoding='utf-8', errors='replace')
    m = re.search(r'id="query">([^<]+)', html)
    if not m:
        return None
    goal = m.group(1).strip()[:120]
    p = _P()
    p.feed(html)
    elems = [e for e in p.elems if e['name']]
    if len(elems) < 2 or len(goal) < 8:
        return None
    g = goal.lower()
    for i, e in enumerate(elems):
        if len(e['name']) > 2 and e['name'].lower() in g:
            return goal, i, elems
    return None


rows = []
for f in sorted(MW.glob('*.html')):
    r = parse(f)
    if not r:
        continue
    goal, idx, elems = r
    refs = [{'ref': j + 1, 'role': e['role'], 'name': e['name']}
            for j, e in enumerate(elems)]
    from dp_cli.decide_train import _render
    rows.append({'query': goal, 'positive': _render(refs[idx]),
                 'negatives': [_render(x) for j, x in enumerate(refs)
                               if j != idx],
                 'src': 'miniwob'})
json.dump(rows, open(OUT, 'w'), ensure_ascii=False)
print(f'{len(rows)} pairs → {OUT}')
