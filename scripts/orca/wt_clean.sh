#!/usr/bin/env bash
# wt_clean.sh — 一鍵清掉所有 swarm/bakeoff worktree（分支保留，供事後考古）
# 用法：make wt-clean
# Idempotent：沒東西可清也安靜成功。

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CLEANED=0

if [ -d "$ROOT/.worktrees" ]; then
  for WT in "$ROOT/.worktrees"/*/; do
    [ -d "$WT" ] || continue
    echo "· 移除 worktree：${WT#"$ROOT"/}"
    git -C "$ROOT" worktree remove --force "$WT" 2>/dev/null || rm -rf "$WT"
    CLEANED=$((CLEANED+1))
  done
fi
git -C "$ROOT" worktree prune

echo "✅ 清完（$CLEANED 個）。分支仍在 — 要一併刪：git branch -D swarm/<x> bakeoff/<x>-<lane>"
