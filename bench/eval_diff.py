#!/usr/bin/env python3
"""eval_decide 复跑 + miss id 集合 diff——供 teacher_hardneg 实验对照。

用法: python bench/eval_diff.py <model_dir>
输出 eval_decide 同口径汇总行 + JSON 行 {miss_ids, ALL, elem, meta}。
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def run(model):
    p = subprocess.run([sys.executable, str(HERE / 'eval_decide.py'), model],
                       capture_output=True, text=True, cwd=str(HERE.parent),
                       env=None)
    out = p.stdout
    print(out)
    res = {'model': model}
    for line in out.splitlines():
        if line.startswith('ALL'):
            res['ALL'] = float(line.split('full=')[1].split('%')[0])
        elif line.startswith('elem'):
            res['elem'] = float(line.split('full=')[1].split('%')[0])
        elif line.startswith('meta'):
            res['meta'] = float(line.split('full=')[1].split('%')[0])
        elif line.startswith('miss:'):
            res['miss_ids'] = eval(line.split('miss:')[1].strip())
    return res


if __name__ == '__main__':
    r = run(sys.argv[1])
    print('JSON:', json.dumps(r, ensure_ascii=False))
