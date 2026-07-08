#!/bin/bash
# ---------------------------------------------------------------------------
# BrowserAct CLI 安裝腳本（Claude Code on the web 用）
#
# 用途：讓每個新的 cloud session 一開始就裝好 `browser-act` 指令，免得每次
#       手動重裝。腳本可重複執行、失敗不會擋住 session 啟動。
#
# 怎麼用：
#   1. 到 claude.ai/code → 環境選擇器 → 齒輪 → **Setup script** 欄位，
#      把本檔內容整段貼上（或貼下面這一行 curl 版）。
#   2. 需要的網路白名單（Network access = Custom）至少要放行：
#        api.browseract.com  www.browseract.com  browseract.com
#        （若也要用瀏覽器開 GitHub 網頁：github.com *.github.com
#          *.githubusercontent.com github.githubassets.com）
#   3. 技能檔本身已進版控（.claude/skills/browser-act/SKILL.md），
#      會自動載入，不需要這支腳本處理。
# ---------------------------------------------------------------------------
set -euo pipefail

# uv 在 Claude Code 雲端環境通常已預裝；沒有的話補裝。
if ! command -v uv >/dev/null 2>&1; then
  echo "[setup] uv 不存在，安裝中…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 安裝（或升級）BrowserAct CLI。已安裝時不視為錯誤。
echo "[setup] 安裝 browser-act-cli…"
uv tool install browser-act-cli --python 3.12 2>/dev/null \
  || uv tool upgrade browser-act-cli 2>/dev/null \
  || true

# 確保 ~/.local/bin 這類 uv tool bin 路徑進到 session 的 PATH。
uv tool update-shell 2>/dev/null || true

if command -v browser-act >/dev/null 2>&1; then
  echo "[setup] browser-act 就緒：$(command -v browser-act)"
else
  echo "[setup] 警告：browser-act 不在 PATH，檢查 ~/.local/bin 是否已加入 PATH"
fi
