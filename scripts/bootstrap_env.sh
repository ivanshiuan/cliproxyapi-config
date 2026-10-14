#!/usr/bin/env bash
# One-command rebuild of the local dev environment from a bare checkout.
#
#   bash scripts/bootstrap_env.sh            # venv + deps + local Postgres + migrations
#   bash scripts/bootstrap_env.sh --no-db    # python env only
#   bash scripts/bootstrap_env.sh --check    # report what is missing, change nothing (exit 1 if any)
#
# Idempotent — safe to re-run. Dev only: refuses to run when RESTO_ENV is not "dev".
#
# What it deliberately does NOT do:
#   * It never creates `.env`. The backend runs on the defaults in restaurant_api/config.py;
#     `.env` is only needed for DevSwarm's ANTHROPIC_API_KEY. Copying .env.example here would
#     put the `sk-ant-xxxx` placeholder in place and fool the SessionStart "API key set" probe.
#   * It never starts docker. On machines with docker use `make db-up` instead of the DB step.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE=full
for arg in "$@"; do
  case "$arg" in
    --no-db) MODE=nodb ;;
    --check) MODE=check ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# Same defaults as restaurant_api/config.py and .github/workflows/ci.yml.
DB_HOST="${RESTO_DB_HOST:-localhost}"
DB_PORT="${RESTO_DB_PORT:-5432}"
DB_USER="${RESTO_DB_USER:-resto}"
DB_PASS="${RESTO_DB_PASSWORD:-resto_dev_password}"
DB_NAME="${RESTO_DB_NAME:-resto_dev}"
VENV="${VENV:-.venv}"
EXTENSIONS=(uuid-ossp citext pg_trgm vector)

MISSING=0
log()  { printf '\033[36m[bootstrap]\033[0m %s\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
miss() { printf '  \033[31mMISS\033[0m %s\n' "$*"; MISSING=$((MISSING + 1)); }
die()  { printf '\033[31m[bootstrap] %s\033[0m\n' "$*" >&2; exit 1; }

if [ "${RESTO_ENV:-dev}" != "dev" ]; then
  die "RESTO_ENV=${RESTO_ENV} — this script creates roles/databases and is dev-only."
fi

# ── python ────────────────────────────────────────────────────────────────
find_python() {
  local c
  for c in "${PYTHON:-}" python3.12 python3; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 \
       && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

check_python() {
  if [ ! -x "$VENV/bin/python" ]; then miss "$VENV/ (virtualenv) not found"; return; fi
  ok "$VENV exists ($("$VENV/bin/python" --version 2>&1))"
  if "$VENV/bin/python" -c 'import devswarm, restaurant_api, fastapi, sqlalchemy, alembic' 2>/dev/null; then
    ok "project + runtime deps importable"
  else
    miss "project deps not installed (pip install -e .)"
  fi
  for t in ruff pyright; do
    if [ -x "$VENV/bin/$t" ]; then ok "$t installed"; else miss "$t not installed"; fi
  done
}

setup_python() {
  local py
  py="$(find_python)" || die "Python >= 3.12 not found (set PYTHON=/path/to/python3.12)."
  if [ ! -x "$VENV/bin/python" ]; then
    log "creating $VENV with $py ($("$py" --version 2>&1))"
    "$py" -m venv "$VENV"
  fi
  log "installing project + pinned lint/type toolchain (requirements-dev.txt) into $VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -e . -r requirements-dev.txt
}

# ── postgres ──────────────────────────────────────────────────────────────
# Superuser psql over the local unix socket (root -> su postgres, else sudo -u postgres).
psql_super() {
  if [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1; then
    su postgres -c "psql -v ON_ERROR_STOP=1 -qtA $(printf '%q ' "$@")"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -n -u postgres psql -v ON_ERROR_STOP=1 -qtA "$@"
  else
    return 99
  fi
}

pg_up() { pg_isready -q -h "$DB_HOST" -p "$DB_PORT" 2>/dev/null; }

check_db() {
  if ! pg_up; then miss "Postgres not accepting connections on $DB_HOST:$DB_PORT"; return; fi
  ok "Postgres accepting connections ($DB_HOST:$DB_PORT)"
  if ! psql_super -c 'select 1' >/dev/null 2>&1; then
    ok "(cannot use superuser access here — skipping role/extension checks)"
  else
    [ "$(psql_super -c "select 1 from pg_roles where rolname='$DB_USER'")" = "1" ] \
      && ok "role $DB_USER" || miss "role $DB_USER"
    [ "$(psql_super -c "select 1 from pg_database where datname='$DB_NAME'")" = "1" ] \
      && ok "database $DB_NAME" || { miss "database $DB_NAME"; return; }
    local e
    for e in "${EXTENSIONS[@]}"; do
      [ "$(psql_super -d "$DB_NAME" -c "select 1 from pg_extension where extname='$e'")" = "1" ] \
        && ok "extension $e" || miss "extension $e"
    done
  fi
  if [ -x "$VENV/bin/alembic" ]; then
    local cur head
    cur="$(cd restaurant_api && ../"$VENV"/bin/alembic current 2>/dev/null | grep -Eo '^[0-9a-f]{8,}' | head -1 || true)"
    head="$(cd restaurant_api && ../"$VENV"/bin/alembic heads 2>/dev/null | grep -Eo '^[0-9a-f]{8,}' | head -1 || true)"
    if [ -n "$head" ] && [ "$cur" = "$head" ]; then ok "alembic at head ($head)"
    else miss "alembic not at head (current=${cur:-none}, head=${head:-?})"; fi
  fi
}

setup_db() {
  if ! pg_up; then
    log "Postgres not running — trying to start it"
    (service postgresql start || sudo -n service postgresql start) >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do pg_up && break; sleep 1; done
  fi
  pg_up || die "Postgres is not reachable on $DB_HOST:$DB_PORT (on docker machines: make db-up)."
  psql_super -c 'select 1' >/dev/null 2>&1 \
    || die "need superuser access to create the role/database (run as root, or passwordless sudo to postgres)."

  # pgvector ships as a separate package on Debian/Ubuntu.
  if [ "$(psql_super -c "select 1 from pg_available_extensions where name='vector'")" != "1" ]; then
    local major
    major="$(( $(psql_super -c 'show server_version_num') / 10000 ))"
    log "pgvector missing — installing postgresql-${major}-pgvector"
    if command -v apt-get >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "postgresql-${major}-pgvector" >/dev/null
    else
      die "install pgvector for Postgres ${major} (Debian/Ubuntu: apt install postgresql-${major}-pgvector) and re-run."
    fi
  fi

  log "ensuring role $DB_USER and database $DB_NAME"
  psql_super -c "DO \$\$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
        CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';
      END IF; END \$\$;"
  if [ "$(psql_super -c "select 1 from pg_database where datname='$DB_NAME'")" != "1" ]; then
    psql_super -c "CREATE DATABASE $DB_NAME OWNER $DB_USER"
  fi
  local e
  for e in "${EXTENSIONS[@]}"; do
    psql_super -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS \"$e\""
  done

  log "applying Alembic migrations (alembic upgrade head)"
  (cd restaurant_api && ../"$VENV"/bin/alembic upgrade head)
}

# ── main ──────────────────────────────────────────────────────────────────
if [ "$MODE" = check ]; then
  log "checking environment (no changes)"
  check_python
  check_db
  if [ "$MISSING" -eq 0 ]; then log "environment is ready."; exit 0; fi
  log "$MISSING item(s) missing — run: bash scripts/bootstrap_env.sh"
  exit 1
fi

setup_python
if [ "$MODE" = full ]; then setup_db; fi

log "done. verify with:  make full-check"
if ! grep -qs '^ANTHROPIC_API_KEY=sk-ant-[A-Za-z0-9_-]\{20,\}' .env 2>/dev/null \
   || grep -qs '^ANTHROPIC_API_KEY=sk-ant-x\{6,\}' .env 2>/dev/null; then
  log "note: no real ANTHROPIC_API_KEY in .env — DevSwarm (make demo/swarm) stays unavailable; the restaurant backend does not need it."
fi
