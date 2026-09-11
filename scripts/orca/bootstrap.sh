#!/usr/bin/env bash
# bootstrap.sh — 一鍵：指揮端（Mac/Linux）Orca 環境就緒檢查 + 安裝（idempotent）
#
# 用法：make orca-bootstrap
#
# 做什麼（重跑不炸，已就緒的項目直接打勾跳過）：
#   1. 檢查/安裝 Orca（macOS 用 brew，失敗退回官方下載頁）
#   2. 檢查 git worktree 支援、.venv、ANTHROPIC_API_KEY、PG
#   3. 印出「在 Orca 註冊 DevSwarm custom agent」要貼的那一條指令
#
# 指揮官 approval 點：Orca 首次開啟時按下「加入 custom agent」的確認 — 僅此一次。

set -uo pipefail   # 不加 -e：檢查項失敗要繼續跑完、最後總結

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
OK=0; WARN=0
pass() { echo "  ✅ $*"; OK=$((OK+1)); }
warn() { echo "  ⚠️  $*"; WARN=$((WARN+1)); }

echo "═══ Orca 指揮端 bootstrap（idempotent，重跑安全）═══"
echo

# ── 1. Orca 本體 ─────────────────────────────────────────────────────────
echo "[1/3] Orca 安裝"
if command -v orca >/dev/null 2>&1 || [ -d "/Applications/Orca.app" ]; then
  pass "Orca 已安裝"
elif [ "$(uname)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
  echo "  · 嘗試 brew 安裝..."
  if brew install --cask orca 2>/dev/null || brew install orca 2>/dev/null; then
    pass "Orca 安裝完成（brew）"
  else
    warn "brew 找不到 Orca formula — 手動下載一次即可：https://github.com/stablyai/orca/releases"
  fi
else
  warn "未偵測到 Orca — 下載：https://github.com/stablyai/orca/releases（Linux headless 見官方 docs）"
fi

# ── 2. 本 repo 前置 ──────────────────────────────────────────────────────
echo "[2/3] repo 前置"
GIT_V="$(git --version | sed 's/git version //')"
case "$GIT_V" in
  1.*|2.[0-4].*) warn "git $GIT_V 過舊，worktree 需要 ≥ 2.5" ;;
  *) pass "git $GIT_V（worktree OK）" ;;
esac
[ -x "$ROOT/.venv/bin/python" ] && pass ".venv 就緒" || warn ".venv 缺 — 跑 make install"
if { [ -f "$ROOT/.env" ] && grep -q '^ANTHROPIC_API_KEY=..*' "$ROOT/.env"; } || [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  pass "ANTHROPIC_API_KEY 已設定"
else
  warn "ANTHROPIC_API_KEY 未設定 — DevSwarm lane 跑不了（.env 填入即可）"
fi
if "$ROOT/.venv/bin/python" -c "import asyncpg" 2>/dev/null && pg_isready -q 2>/dev/null; then
  pass "PostgreSQL 活著（pytest gate 可跑）"
else
  warn "PG 未起或無法確認 — sudo service postgresql start（沒 PG 只影響整合測 gate）"
fi
mkdir -p "$ROOT/.worktrees" && pass ".worktrees/ 就緒（已 gitignore）"

# ── 3. Orca custom agent 註冊資訊 ────────────────────────────────────────
echo "[3/3] Orca 內註冊 DevSwarm（首次一次性，貼這條當 agent command）"
echo
echo "    ┌─────────────────────────────────────────────────────────┐"
echo "      name:    DevSwarm"
echo "      command: make swarm-wt SPEC={spec} PUSH=1"
echo "      cwd:     $ROOT"
echo "    └─────────────────────────────────────────────────────────┘"
echo
echo "    （{spec} 換成任一 specs/*.md；Orca 支援任何 CLI agent，"
echo "      這條指令內建 worktree 隔離 + promote + gate + commit + PR。）"
echo

echo "═══ 總結：$OK 項就緒、$WARN 項待處理 ═══"
[ $WARN -eq 0 ] && echo "全綠 — 可直接開 Orca 試駕（docs/20 Phase O-0）。"
exit 0
