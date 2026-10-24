#!/usr/bin/env python3
"""merge 守卫——训练集合并的唯一入口：每行入合并前过 eval 撞车检测
（精确+子串，复用 check_leakage 同规则），命中行剔除并登记，绝不静默入训。"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from eval_cases import EVAL_CASES

def _hit(q, goals):
    qs = q.strip()
    return any(qs == g or (len(qs) > 4 and (qs in g or g in qs))
               for g in goals)

_BAD_SRC = {'meta', 'synthetic'}      # v9 毒化根因——伪 meta 样本禁入训


def merge(paths: list[str], out: str, allow_bad_src: bool = False):
    goals = {c['goal'].strip() for c in EVAL_CASES}
    rows, dropped, skipped = [], [], []
    for p in paths:
        for r in json.load(open(p)):
            if not allow_bad_src and r.get('src') in _BAD_SRC:
                skipped.append(r)
            elif _hit(r['query'], goals):
                dropped.append(r)
            else:
                rows.append(r)
    json.dump(rows, open(out, 'w'), ensure_ascii=False)
    print(f'merged {len(rows)} rows → {out}'
          f'（剔除 {len(dropped)} 撞车+{len(skipped)} src 拒收）')
    for r in dropped:
        print('  drop:', r['query'])
    if skipped:
        print(f'  src 拒收: {len(skipped)} 行（meta/synthetic——v9 毒化纪律）')

if __name__ == '__main__':
    allow = '--allow-bad-src' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    merge(args[1:], args[0], allow_bad_src=allow)
