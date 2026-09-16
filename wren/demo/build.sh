#!/usr/bin/env bash
# 重建 demo 資料(data.duckdb)+ 編譯 MDL 語意層。可重複執行。
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"
uv run --no-project --quiet --with duckdb python seed/build_duckdb.py
wren context validate   # 先驗證 MDL(YAML 結構 + view dry-plan),再編譯
wren context build
echo
echo "查詢範例:"
echo "  wren --sql \"SELECT s.name AS store, s.city, COUNT(o.order_id) AS orders, SUM(o.total) AS revenue FROM orders o JOIN stores s ON o.store_id=s.store_id GROUP BY 1,2 ORDER BY revenue DESC\" \\"
echo "    --connection-info '{\"datasource\":\"duckdb\",\"url\":\"$here\",\"format\":\"duckdb\"}' -o table"
