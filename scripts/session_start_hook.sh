#!/usr/bin/env bash
# SessionStart hook — 開新 session 時自動注入 JIT context
# 由 .claude/settings.json 的 SessionStart hooks 呼叫

set -u
REPO="/home/user/cliproxyapi-config"
cd "$REPO" 2>/dev/null || exit 0

# ── 環境健康檢查 ────────────────────────────────
sudo service postgresql start >/dev/null 2>&1 || true
if pg_isready >/dev/null 2>&1; then
  echo '✅ PG up'
else
  echo '⚠️ PG down — run: sudo service postgresql start'
fi

if grep -q 'ANTHROPIC_API_KEY=sk-' "$REPO/.env" 2>/dev/null; then
  echo '✅ API key set'
else
  echo '⚠️ ANTHROPIC_API_KEY missing — DevSwarm unavailable until you fill .env'
fi

# ── JIT context 注入 ──────────────────────────
echo ''
echo '📝 最近 3 commits:'
git log --oneline -3 2>/dev/null | sed 's/^/  /' || echo '  (git log 讀不到)'

if [ -f "$REPO/COMMANDER_HANDOFF.md" ]; then
  echo ''
  echo '📋 HANDOFF 開頭:'
  head -15 "$REPO/COMMANDER_HANDOFF.md" 2>/dev/null | sed 's/^/  /'
fi

# ── Harness 提醒（若在跑非 Opus 4.7 的模型時建議啟用）───
echo ''
echo '💡 Opus 4.8 建議啟用行為紀律: /output-style opus-harness'
echo '💡 大任務？考慮 orchestrator 模式（讀 .claude/skills/orchestrator-mode/SKILL.md）'
