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


def to_pairs(rows: list[dict]) -> list[dict]:
    pairs = []
    for r in rows:
        refs = (r.get("state") or {}).get("refs") or []
        ch = next((x for x in refs if x.get("chosen")), None)
        if not ch or not ch.get("name"):
            # 脱敏态 refs name=''（DPX_REFS_PRIVATE 默认开）——无文本对是噪声必拒
            continue
        pos = f"{ch.get('role', '')} {ch.get('name', '')}".strip()
        if not pos:
            continue
        negs = [
            f"{x.get('role', '')} {x.get('name', '')}".strip()
            for x in refs
            if x["ref"] != ch["ref"] and x.get("name")
        ]
        q = r.get("goal") or (r.get("chosen") or "").split(" -S ")[0]
        if not q or not negs:
            continue
        pairs.append({"query": q, "positive": pos, "negatives": negs, "src": "verified"})
    return pairs


def main() -> int:
    src = next(
        (a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--src=")),
        str(Path.home() / ".dp_cli/sessions/decide_train.jsonl"),
    )
    out = next(
        (a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--out=")), None
    )
    rows = [json.loads(l) for l in open(src) if l.strip()]
    ver = verified_rows(rows)
    pairs = to_pairs(ver)
    print(f"verified rows: {len(ver)}/{len(rows)}  pairs: {len(pairs)}")
    if out:
        json.dump(pairs, open(out, "w"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
