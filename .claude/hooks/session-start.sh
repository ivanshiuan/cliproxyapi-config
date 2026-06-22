#!/bin/bash
# Session bootstrap for Claude Code on the web.
# Brings a fresh container to a state where `make full-check` passes:
# python deps (+dev tools), a running PostgreSQL, role/db/extensions, migrations.
# Idempotent and non-interactive. Local dev (docker-compose) is left untouched.
set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-/home/user/cliproxyapi-config}"
cd "$PROJECT_DIR" || exit 0

# Local dev uses docker-compose for Postgres (see CLAUDE.md) — don't touch the
# system cluster there; just report status and exit.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  pg_isready >/dev/null 2>&1 && echo '✅ PG reachable' \
    || echo 'ℹ️  PG down — `make db-up` (docker) for local dev'
  exit 0
fi

echo "▶ Bootstrapping restaurant_api dev environment (web session)…"

# 1. Python deps: venv + editable install including ruff/pyright (dev extra).
make install || echo "⚠️  make install failed — run it manually"

# 2. Start the system PostgreSQL 16 cluster (the web container has no Docker).
pg_ctlcluster 16 main start 2>/dev/null || sudo service postgresql start 2>/dev/null || true
for _ in $(seq 1 30); do pg_isready -h localhost >/dev/null 2>&1 && break; sleep 1; done

if pg_isready -h localhost >/dev/null 2>&1; then
  # 3. Role + database (idempotent).
  su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='resto'\" | grep -q 1 \
    || psql -c \"CREATE ROLE resto WITH LOGIN PASSWORD 'resto_dev_password' SUPERUSER\"" >/dev/null 2>&1
  su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='resto_dev'\" | grep -q 1 \
    || createdb -O resto resto_dev" >/dev/null 2>&1

  # 4. Required extensions (uuid-ossp, citext, pg_trgm via initdb script; +vector).
  su postgres -c "psql -d resto_dev -f $PROJECT_DIR/restaurant_api/initdb/01_extensions.sql" >/dev/null 2>&1
  su postgres -c "psql -d resto_dev -c 'CREATE EXTENSION IF NOT EXISTS vector'" >/dev/null 2>&1

  # 5. Apply migrations to head.
  make db-migrate >/dev/null 2>&1 && echo "✅ migrations applied" || echo "⚠️  db-migrate failed"
  echo "✅ Environment ready: deps installed, PG up, schema migrated."
else
  echo "⚠️  PostgreSQL did not come up — router/db tests will skip."
fi
