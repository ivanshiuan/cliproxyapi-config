#!/bin/sh
# Container startup: migrate → seed → serve.
# PORT is injected by Render (free tier: random); defaults to 8000 locally.
set -e

cd /app/restaurant_api
/opt/venv/bin/alembic upgrade head || true

cd /app
/opt/venv/bin/python scripts/seed_wheel_campaign.py || true

exec /opt/venv/bin/uvicorn restaurant_api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips '*'
