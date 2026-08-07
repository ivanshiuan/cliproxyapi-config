#!/bin/bash
# ---------------------------------------------------------------------------
# WrenAI（wren CLI）安裝腳本（Claude Code on the web 用）
#
# 用途：讓每個新的 cloud session 一開始就裝好 `wren` 指令，免得每次手動重裝。
#       WrenAI 是「語意化 SQL 層 / GenBI 引擎」——用自然語言問資料、
#       它幫你轉成 SQL 打進 22+ 種資料庫（Postgres、MySQL、BigQuery…）。
#       腳本可重複執行、失敗不會擋住 session 啟動。
#
# 為什麼需要這支：容器是 ephemeral，`wren` 執行檔裝在 ~/.local（不進版控），
#       session 回收就沒了。**技能檔本身已進版控**
#       （.claude/skills/wren/SKILL.md + .agents/skills/wren/ + skills-lock.json），
#       fresh clone 會自動載入，所以這支只負責把「CLI 執行檔」補回來。
#
# 怎麼用：
#   1. 到 claude.ai/code → 環境選擇器 → 齒輪 → **Setup script** 欄位，
#      把本檔內容整段貼上（或跑一行：bash scripts/setup_wren.sh）。
#   2. 需要的網路白名單（Network access = Custom）至少要放行：
#        pypi.org  files.pythonhosted.org        # pip / uv 下載 wrenai
#        github.com *.githubusercontent.com       # npx skills 重新同步技能（選用）
#      要真正用 `wren ask` / genbi 讓 Claude 產 SQL，還要放行 LLM 供應商：
#        api.anthropic.com                        # 用 Claude 當 text-to-SQL 引擎
# ---------------------------------------------------------------------------
set -euo pipefail

# 選用哪些 extras：
#   postgres → 接 restaurant_api 的 PG（本專案主力資料庫）
#   memory   → 用 LanceDB 做 schema/查詢語意記憶（wren memory / wren ask 需要）
#   mcp      → 用 `wren serve mcp` 把 Wren 當 MCP server 給 agent 用
# 要接別的資料源再加，例如：wrenai[postgres,mysql,bigquery,memory,mcp]
WREN_SPEC="wrenai[postgres,memory,mcp]"

# uv 在 Claude Code 雲端環境通常已預裝；沒有的話補裝。
if ! command -v uv >/dev/null 2>&1; then
  echo "[setup] uv 不存在，安裝中…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 安裝（或升級）wren CLI。已安裝時不視為錯誤。
echo "[setup] 安裝 ${WREN_SPEC}…"
uv tool install "${WREN_SPEC}" 2>/dev/null \
  || uv tool upgrade wrenai 2>/dev/null \
  || true

# 確保 ~/.local/bin 這類 uv tool bin 路徑進到 session 的 PATH。
uv tool update-shell 2>/dev/null || true

if command -v wren >/dev/null 2>&1; then
  echo "[setup] wren 就緒：$(command -v wren) （$(wren --version 2>/dev/null)）"
  echo "[setup] 下一步：wren skills get onboarding  # 端到端把資料庫接上"
else
  echo "[setup] 警告：wren 不在 PATH，檢查 ~/.local/bin 是否已加入 PATH"
fi
