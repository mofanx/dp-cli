#!/usr/bin/env python3
"""chosen→verified 核验层：把 decide_train 行升级为 verified 训练对。

升级判据（弱标转强标）：
- decide 行：goal 存在 + 候选中带 chosen 标记 + post_delta 非空
  （执行后确有页面效果 → 选中正确性被行为结果验证）
- 非 decide 命令行：post_delta 非空（chosen argv 被真执行出效果）

输出与 export_pairs 同构的 {query, positive, negatives[], src='verified'}。
用法：
    python3 bench/verify_pairs.py [--src ~/.dp_cli/sessions/decide_train.jsonl]
                                  [--out /tmp/verified_pairs.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def verified_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        refs = (r.get("state") or {}).get("refs") or []
        if r.get("goal") and any(x.get("chosen") for x in refs) and r.get("post_delta"):
            out.append(r)
        elif not r.get("goal") and r.get("post_delta"):
            out.append(r)
    return out


_POS_DUP_CAP = 3     # m2w 对中 positive 最大重复 3——real 对同元素跨步重复 ≤cap 防单点虚胖
_POS_GLOBAL_CAP = 10 # 同 positive 跨无关 goal 全标正是"通用导航总产 delta"伪影——全局封顶
_NEG_CAP = 24        # m2w 负例密度


def _rank_negs(refs: list[dict], ch: dict) -> list[str]:
    """难负例对齐：同 role 优先采样（m2w ~64% hard neg 形态），异 role 序贯补齐至 _NEG_CAP。"""
    prole = ch.get("role", "")
    same, other = [], []
    for x in refs:
        if x["ref"] == ch["ref"] or not x.get("name"):
            continue
        t = f"{x.get('role', '')} {x['name']}".strip()
        (same if x.get("role", "") == prole else other).append(t)
    return (same + other)[:_NEG_CAP]


def to_pairs(rows: list[dict], weak: bool = False, dedupe: bool = True) -> list[dict]:
    pairs = []
    pos_seen: dict[tuple[str, str], int] = {}
    pos_global: dict[str, int] = {}
    for r in rows:
        refs = (r.get("state") or {}).get("refs") or []
        ch = next((x for x in refs if x.get("chosen")), None)
        w = False
        if not ch and r.get("engine_ref") and weak:
            # 低置信 decide 未执行——engine 选中作 weak 标（降权而非丢弃）
            ch = next((x for x in refs if x.get("ref") == r["engine_ref"]), None)
            w = True
        if not ch or not ch.get("name"):
            # 脱敏态 refs name=''（DPX_REFS_PRIVATE 默认开）——无文本对是噪声必拒
            continue
        pos = f"{ch.get('role', '')} {ch.get('name', '')}".strip()
        if not pos:
            continue
        negs = _rank_negs(refs, ch)
        q = r.get("goal") or (r.get("chosen") or "").split(" -S ")[0]
        if not q or not negs:
            continue
        if dedupe:
            k = (q, pos)
            if pos_seen.get(k, 0) >= _POS_DUP_CAP or pos_global.get(pos, 0) >= _POS_GLOBAL_CAP:
                continue
            pos_seen[k] = pos_seen.get(k, 0) + 1
            pos_global[pos] = pos_global.get(pos, 0) + 1
        p = {"query": q, "positive": pos, "negatives": negs, "src": "verified"}
        if w:
            p["weak"] = True
        pairs.append(p)
    return pairs


def split_holdout(pairs: list[dict], ratio: float = 0.2) -> tuple[list[dict], list[dict]]:
    """按 goal 所在站（query 内 site 不可知——用 goal 首词/域名近似不可行，
    改用行级 site 字段已剥：fallback 按 goal 文本聚簇切分防同 goal 跨集）。"""
    import hashlib
    seen: dict[str, int] = {}
    holdout, train = [], []
    for p in pairs:
        key = p["query"]
        if key not in seen:
            # hashlib 确定性摘要——str hash 跨进程随机会致 held-out 漂移泄漏
            seen[key] = 1 if int(hashlib.md5(key.encode()).hexdigest(), 16) % 5 == 0 else 0
        (holdout if seen[key] else train).append(p)
    return train, holdout


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="verify_pairs.py")
    ap.add_argument("--src", default=str(Path.home() / ".dp_cli/sessions/decide_train.jsonl"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--weak", action="store_true", help="engine_ref 无执行证据的行作 weak 标回收")
    ap.add_argument("--holdout-out", default=None, help="输出 20%% goal 隔离 held-out 集路径")
    a = ap.parse_args()
    src, out = a.src, a.out
    rows = [json.loads(l) for l in open(src) if l.strip()]
    ver = verified_rows(rows)
    pairs = to_pairs(ver, weak=a.weak)
    print(f"verified rows: {len(ver)}/{len(rows)}  pairs: {len(pairs)}"
          f"  weak: {sum(1 for p in pairs if p.get('weak'))}")
    if a.holdout_out:
        train, holdout = split_holdout(pairs)
        json.dump(holdout, open(a.holdout_out, "w"), ensure_ascii=False)
        pairs = train
        print(f"held-out: {len(holdout)}  train: {len(train)}")
    if out:
        json.dump(pairs, open(out, "w"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
