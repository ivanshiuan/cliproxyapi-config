#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# WrenAI 一鍵起（冪等）—— 裝好 CLI + 建 demo + 驗證查詢,一條指令、可重複跑。
#
#   make wren     （或）    bash scripts/wren_up.sh
#
# 設計原則:Ivan 不執行 runbook,只按最終 approval。這支把
#   1) 裝/升級 wren CLI（scripts/setup_wren.sh）
#   2) 從 seed 重建 demo 資料 + 編譯 MDL 語意層（wren/demo/build.sh）
#   3) 跑「每家店營收」驗證查詢並印出結果
# 全包成單一冪等步驟。任何一步真的失敗就非零離開。
# ---------------------------------------------------------------------------
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 1) CLI（不在就裝、在就跳過/升級);setup_wren.sh 失敗會 exit 1。
bash "$root/scripts/setup_wren.sh"
export PATH="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"):$PATH"

# 2) demo 資料 + MDL。
bash "$root/wren/demo/build.sh" >/dev/null

# 3) 驗證:自然語言問題 → 受治理 SQL → Wren 引擎跑真實資料。
#    WREN_PROJECT_HOME 讓 wren 從任何 cwd 都找得到這個專案的 MDL。
export WREN_PROJECT_HOME="$root/wren/demo"
echo "── WrenAI demo:每家店營收(問題→受治理 SQL→Wren MDL)──"
wren --sql "SELECT s.name AS store, s.city, COUNT(o.order_id) AS orders, SUM(o.total) AS revenue FROM orders o JOIN stores s ON o.store_id = s.store_id GROUP BY 1, 2 ORDER BY revenue DESC" \
  --connection-info "{\"datasource\":\"duckdb\",\"url\":\"$root/wren/demo\",\"format\":\"duckdb\"}" \
  -o table
