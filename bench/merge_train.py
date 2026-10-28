#!/usr/bin/env python3
"""merge 守卫——训练集合并的唯一入口：每行入合并前过 eval 撞车检测
（精确+子串，复用 check_leakage 同规则），命中行剔除并登记，绝不静默入训。"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from eval_cases import EVAL_CASES
from verified_data import eligible

def _hit(q, goals):
    qs = q.strip()
    return any(qs == g or (len(qs) > 4 and (qs in g or g in qs))
               for g in goals)

def _say(msg: str) -> None:
    """GBK/cp1252 控制台兜底——中文/→ 直写 UTF-8 字节，
    绕开 Windows locale 编码炸；无 .buffer（StringIO 等）回退 print。"""
    buf = getattr(sys.stdout, 'buffer', None)
    if buf is None:
        print(msg)
        return
    buf.write((msg + '\n').encode('utf-8', errors='replace'))
    buf.flush()

def merge(paths: list[str], out: str, allow_bad_src: bool = False,
          allow_derived: bool = False):
    if allow_bad_src:
        _say('WARNING: legacy override permits all unverified sources; '
             'results are not clean training data')
    goals = {c['goal'].strip() for c in EVAL_CASES}
    rows, dropped, skipped = [], [], []
    for p in paths:
        # 数据文件契约 UTF-8——locale 默认编码（Windows cp1252/cp936）
        # 读写中文 query 会 charmap 炸，必须显式 encoding
        for r in json.load(open(p, encoding='utf-8')):
            if (not allow_bad_src and not eligible(r)) \
                    or (not allow_derived
                        and r.get('prov') == 'record_derived'):
                skipped.append(r)
            elif _hit(r['query'], goals):
                dropped.append(r)
            else:
                rows.append(r)
    with open(out, 'w', encoding='utf-8') as fp:
        json.dump(rows, fp, ensure_ascii=False)
    _say(f'merged {len(rows)} rows → {out}'
         f'（剔除 {len(dropped)} 撞车+{len(skipped)} src 拒收）')
    for r in dropped:
        _say(f"  drop: {r['query']}")
    if skipped:
        _say(f'  src 拒收: {len(skipped)} 行（默认仅接收有执行后证据的 '
             f'verified_real；快照推导行须显式 --allow-derived；'
             f'旧实验须显式 --allow-unverified'
             f'（旧名 --allow-bad-src））')

if __name__ == '__main__':
    allow = ('--allow-unverified' in sys.argv
             or '--allow-bad-src' in sys.argv)
    allow_d = '--allow-derived' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    merge(args[1:], args[0], allow_bad_src=allow,
          allow_derived=allow_d)
