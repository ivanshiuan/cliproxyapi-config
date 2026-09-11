#!/usr/bin/env bash
# BUFF OS 一鍵安裝 — 同步 code、重建 pgvector DB、跑 migrations、驗證表。
#
#   bash scripts/buff_os_setup.sh
#
# 安全提醒：step 2 會 `down -v` 砍掉本機開發資料庫的所有資料重來。
# 開發期資料都可從 ingest 重灌，所以沒差；等 DB 有真實資料後改用
# make db-migrate 就好，不要再跑本腳本。

set -euo pipefail
cd "$(dirname "$0")/.."

# hermes-agent 等其他工具可能污染 PYTHONPATH — 一律清掉。
unset PYTHONPATH 2>/dev/null || true

BRANCH=claude/notebooklm-mcp-buff-os-xb415g
DB_USER="${RESTO_DB_USER:-resto}"
DB_NAME="${RESTO_DB_NAME:-resto_dev}"

echo "==> 1/6 同步 code ($BRANCH)"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull origin "$BRANCH"

echo "==> 2/6 重建資料庫容器（pgvector image）"
docker compose -f restaurant_api/docker-compose.yml down -v
docker compose -f restaurant_api/docker-compose.yml up -d

echo "==> 3/6 等 Postgres 就緒"
for _ in $(seq 1 60); do
  if docker exec resto-db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec resto-db pg_isready -U "$DB_USER" -d "$DB_NAME"

echo "==> 4/6 安裝套件進 .venv"
if [ ! -x .venv/bin/python ]; then
  python3.12 -m venv .venv
fi
.venv/bin/pip install -q -e .

echo "==> 5/6 跑 migrations"
(cd restaurant_api && ../.venv/bin/alembic upgrade head)

echo "==> 6/6 驗證 BUFF OS 表"
docker exec resto-db psql -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT tablename FROM pg_tables WHERE tablename LIKE 'knowledge%' OR tablename = 'brief_runs' ORDER BY 1;"

echo ""
echo "✅ BUFF OS setup 完成。下一步："
echo "   灌資料：  .venv/bin/python scripts/ingest_knowledge.py --file <檔>.md --scope funding"
echo "   跑 brief：.venv/bin/python scripts/run_brief.py today_top5"
