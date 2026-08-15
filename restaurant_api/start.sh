#!/bin/sh
# Container startup: migrate → seed → serve.
# PORT is injected by Render (free tier: random); defaults to 8000 locally.
set -e

# Migrations MUST succeed. A swallowed failure here (the old `|| true`) is what
# let a tableless DB serve 500s on every data route while /health/live stayed
# green — fail the boot loudly instead so a broken migration is visible.
cd /app/restaurant_api
/opt/venv/bin/alembic upgrade head

# Seeding is best-effort — a re-seed race or transient hiccup shouldn't take the
# service down, and the campaign is idempotent on its slug.
cd /app
/opt/venv/bin/python scripts/seed_wheel_campaign.py || true

exec /opt/venv/bin/uvicorn restaurant_api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips '*'
