#!/usr/bin/env bash
# sync-skill.sh [--check]
#
# 把仓库内 skills/dp-cli/ 同步到 ~/.claude/skills/dp-cli/，
# 末尾 diff -r 校验并回显结果。
#
#   scripts/sync-skill.sh           # 同步 + 校验
#   scripts/sync-skill.sh --check   # 只校验不同步：一致 exit 0，漂移 exit 1
#
# 目标目录可用 DP_SKILL_DST 覆盖（测试/多环境）。
# ~/.claude 不存在时 --check 视为不适用（exit 0 SKIP），
# 避免无 skill 安装的环境（CI）误伤。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/dp-cli"
DST="${DP_SKILL_DST:-$HOME/.claude/skills/dp-cli}"

check_only=0
[ "${1:-}" = "--check" ] && check_only=1

# 忽略 dp_cli_version 戳行——install --skills 会重写为包版本，
# 与 repo 占位 "dev" 的差异是合法状态（同 skill_dirs_equal 归一化语义）
DIFF="diff -r -I ^dp_cli_version:"

if [ "$check_only" = 1 ]; then
    if [ ! -d "$DST" ]; then
        echo "SKIP: $DST 不存在（无 skill 安装环境）"
        exit 0
    fi
    if $DIFF "$SRC" "$DST" >/dev/null 2>&1; then
        echo "IN SYNC: $SRC == $DST"
        exit 0
    fi
    echo "OUT OF SYNC: $SRC != $DST" >&2
    DIFF_OUT="$($DIFF "$SRC" "$DST" 2>&1 || true)"
    printf '%s\n' "$DIFF_OUT" | head -30 >&2
    echo "→ 请运行 scripts/sync-skill.sh 同步" >&2
    exit 1
fi

mkdir -p "$DST"
rsync -a --delete "$SRC/" "$DST/"
# 同步后给 dst 打上当前包版本戳（同 install --skills 语义），
# 否则 dev 占位戳会触发 warn_drift 每版本一次的漂移警告
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -1)"
if [ -n "$VER" ] && [ -f "$DST/SKILL.md" ]; then
    sed -i "s/^dp_cli_version:.*/dp_cli_version: $VER/" "$DST/SKILL.md"
fi
if $DIFF "$SRC" "$DST" >/dev/null 2>&1; then
    echo "SYNCED & VERIFIED: $SRC -> $DST"
else
    echo "VERIFY FAILED: rsync 后仍有差异" >&2
    DIFF_OUT="$($DIFF "$SRC" "$DST" 2>&1 || true)"
    printf '%s\n' "$DIFF_OUT" | head -30 >&2
    exit 1
fi
