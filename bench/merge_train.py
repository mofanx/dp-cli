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

def merge(paths: list[str], out: str):
    goals = {c['goal'].strip() for c in EVAL_CASES}
    rows, dropped = [], []
    for p in paths:
        for r in json.load(open(p)):
            (dropped if _hit(r['query'], goals) else rows).append(r)
    json.dump(rows, open(out, 'w'), ensure_ascii=False)
    print(f'merged {len(rows)} rows → {out}（剔除 {len(dropped)} 撞车行）')
    for r in dropped:
        print('  drop:', r['query'])

if __name__ == '__main__':
    merge(sys.argv[2:], sys.argv[1])
