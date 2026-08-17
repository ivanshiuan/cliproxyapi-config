#!/usr/bin/env bash
# swarm_worktree.sh — 一鍵：在隔離 git worktree 裡跑 DevSwarm → promote → gate → commit
#
# 用法（經 make，指揮官只跑這一條）：
#   make swarm-wt SPEC=specs/profit_calc.md            # 全流程
#   make swarm-wt SPEC=... BUDGET=3                    # 覆寫預算（預設 5.0 USD）
#   make swarm-wt SPEC=... FRESH=1                     # 砍掉重建 worktree（丟棄舊產出）
#   make swarm-wt SPEC=... PUSH=1                      # 完成後 push 分支（approval = 審 PR）
#   make swarm-wt SPEC=... SETUP_ONLY=1                # 只建好 worktree（給 Orca UI 掛其他 agent 用）
#   make swarm-wt SPEC=... DRY_RUN=1                   # 只跑 PM+Architect 出 PRD，不寫碼
#
# Idempotent 保證：
#   · 同一 SPEC 重跑 → 重用同一 worktree + 分支，promote 覆寫同目的檔
#   · worktree/分支不存在就建、存在就用；FRESH=1 才重建
#   · 沒有新變更時 commit 步驟安靜跳過
#
# 指揮官的 approval 點只有一個：審 PR、按 Merge。其餘全自動。
#
# 相容 macOS bash 3.2（不用 bash 4 語法）。

set -euo pipefail

SPEC="${1:-}"
BUDGET="${BUDGET:-5.0}"
FRESH="${FRESH:-0}"
PUSH="${PUSH:-0}"
SETUP_ONLY="${SETUP_ONLY:-0}"
DRY_RUN="${DRY_RUN:-0}"

die() { echo "❌ $*" >&2; exit 1; }
info() { echo "· $*"; }

[ -n "$SPEC" ] || die "usage: swarm_worktree.sh specs/<name>.md（或 make swarm-wt SPEC=...）"

ROOT="$(git rev-parse --show-toplevel)"
[ -f "$ROOT/$SPEC" ] || [ -f "$SPEC" ] || die "spec 不存在：$SPEC"
# 正規化成 repo 相對路徑
case "$SPEC" in
  /*) SPEC="$(python3 -c "import os,sys;print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$SPEC" "$ROOT")" ;;
esac

SLUG="$(basename "$SPEC" .md)"
WT_NAME="${WT_NAME:-swarm-$SLUG}"
BRANCH="${BRANCH:-swarm/$SLUG}"
WT_DIR="$ROOT/.worktrees/$WT_NAME"
VENV_PY="$ROOT/.venv/bin/python"

# ── 前置檢查（SETUP_ONLY 只備 worktree，不需要 venv / API key）────────────
if [ "$SETUP_ONLY" != "1" ]; then
  [ -x "$VENV_PY" ] || die ".venv 不存在 — 先跑 make install"
  # devswarm CLI 自己也會擋，這裡提早給人話錯誤
  if [ -f "$ROOT/.env" ] && grep -q '^ANTHROPIC_API_KEY=..*' "$ROOT/.env"; then :;
  elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then :;
  else die "ANTHROPIC_API_KEY 未設定（.env 或環境變數）— DevSwarm 跑不了"; fi
fi

# ── worktree 準備（idempotent）────────────────────────────────────────────
mkdir -p "$ROOT/.worktrees"

if [ "$FRESH" = "1" ] && [ -d "$WT_DIR" ]; then
  info "FRESH=1 → 移除舊 worktree $WT_NAME"
  git -C "$ROOT" worktree remove --force "$WT_DIR" 2>/dev/null || rm -rf "$WT_DIR"
  git -C "$ROOT" worktree prune
  git -C "$ROOT" branch -D "$BRANCH" 2>/dev/null || true
fi

if [ -d "$WT_DIR" ]; then
  git -C "$WT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "$WT_DIR 存在但不是 git worktree — 手動清掉或 FRESH=1"
  info "重用既有 worktree：$WT_DIR（分支 $BRANCH）"
else
  if git -C "$ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    info "分支 $BRANCH 已存在 → 掛回 worktree"
    git -C "$ROOT" worktree add "$WT_DIR" "$BRANCH"
  else
    info "建立 worktree $WT_NAME（新分支 $BRANCH，基於目前 HEAD）"
    git -C "$ROOT" worktree add -b "$BRANCH" "$WT_DIR"
  fi
fi

# spec 還沒 commit 進基準分支時，worktree 裡會缺檔 → 從主樹帶過去
if [ ! -f "$WT_DIR/$SPEC" ] && [ -f "$ROOT/$SPEC" ]; then
  mkdir -p "$(dirname "$WT_DIR/$SPEC")"
  cp "$ROOT/$SPEC" "$WT_DIR/$SPEC"
  info "spec 尚未 commit — 已複製進 worktree"
fi

if [ "$SETUP_ONLY" = "1" ]; then
  echo
  echo "✅ worktree 就緒（SETUP_ONLY）："
  echo "   路徑：$WT_DIR"
  echo "   分支：$BRANCH"
  echo "   → 在 Orca 把任何 agent 的工作目錄指到這裡即可"
  exit 0
fi

# ── 跑 DevSwarm（產出落在 worktree 內的 workspace/，gitignored）──────────
info "DevSwarm 起跑：spec=$SPEC budget=\$$BUDGET"
SWARM_ARGS=(--task-file "$WT_DIR/$SPEC" --budget "$BUDGET" --workspace-root "$WT_DIR/workspace" --verbose)
[ "$DRY_RUN" = "1" ] && SWARM_ARGS=("${SWARM_ARGS[@]}" --dry-run)

set +e
( cd "$WT_DIR" && "$VENV_PY" -m devswarm "${SWARM_ARGS[@]}" )
SWARM_EXIT=$?
set -e

if [ "$DRY_RUN" = "1" ]; then
  echo "✅ dry-run 完成（只出 PRD/架構，未寫碼）。滿意就拿掉 DRY_RUN=1 重跑同一條指令。"
  exit $SWARM_EXIT
fi
[ $SWARM_EXIT -eq 0 ] || die "DevSwarm 未通過（exit $SWARM_EXIT）— 看上方 QA report；修 spec 後重跑同一條指令即可（idempotent）"

# ── promote（用 worktree 自己的 promote.py，一切落在 worktree 內）────────
TASK_DIR="$(ls -td "$WT_DIR/workspace"/*/ 2>/dev/null | head -1 || true)"
[ -n "$TASK_DIR" ] || die "找不到 workspace 產出目錄"
TASK_ID="$(basename "$TASK_DIR")"
info "promote task $TASK_ID → worktree 的 restaurant_api/"
( cd "$WT_DIR" && "$VENV_PY" scripts/promote.py "$TASK_ID" )

# ── gate：ruff（pytest 兩道已由 promote 內建 gate 跑過）──────────────────
info "gate：ruff check"
( cd "$WT_DIR" && "$VENV_PY" -m ruff check restaurant_api tests ) \
  || die "ruff 未過 — 產出留在 $WT_DIR，修完重跑同一條指令"

# ── commit（無變更則跳過）────────────────────────────────────────────────
cd "$WT_DIR"
git add -A
if git diff --cached --quiet; then
  info "無新變更（重跑同結果）— 跳過 commit"
else
  git commit -m "feat(services): promote $SLUG from DevSwarm task $TASK_ID"
  info "已 commit 到分支 $BRANCH"
fi

# ── 推送 + 開 PR（approval 入口）─────────────────────────────────────────
if [ "$PUSH" = "1" ]; then
  git push -u origin "$BRANCH"
  if command -v gh >/dev/null 2>&1; then
    gh pr create --fill --head "$BRANCH" 2>/dev/null || info "PR 已存在或無法自動開 — 用下方連結手動確認"
  fi
  REMOTE_URL="$(git remote get-url origin | sed -e 's#git@github.com:#https://github.com/#' -e 's#\.git$##')"
  echo
  echo "✅ 全部完成。指揮官唯一要做的事：審 PR、按 Merge"
  echo "   $REMOTE_URL/compare/$BRANCH?expand=1"
else
  echo
  echo "✅ 完成（本機分支 $BRANCH）。要進 approval 流程：重跑加 PUSH=1，或："
  echo "   git -C $WT_DIR push -u origin $BRANCH"
fi
