# TIGER LINE PRIME — Sprint 7 web deployment container.
#
# Multi-stage minimal image: no repo checkout, no dev deps, no DB drivers.
# Runs as non-root under tini for PID-1 signal handling. Single process:
# uvicorn serving the FastAPI app from tigerline.web.
#
# Build (from repo root):
#   docker build -f tigerline/Dockerfile -t tigerline:latest .
#
# Run locally:
#   docker run -p 8000:8000 tigerline:latest
#   open http://localhost:8000
#
# Deploy to Fly.io: see tigerline/docs/DEPLOY.md.

# ── Builder: install runtime deps into a wheelhouse ─────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

RUN pip install --upgrade pip

# Only the packages tigerline.web actually imports at runtime — we do NOT
# install the heavy devswarm / restaurant_api chain (langgraph, sqlalchemy,
# asyncpg, alembic, ...). Keep the image small and the attack surface tiny.
RUN pip wheel --wheel-dir /wheels \
    "fastapi>=0.115,<1.0" \
    "uvicorn[standard]>=0.32,<1.0" \
    "pydantic>=2.5,<3.0" \
    "pyyaml>=6.0,<7.0" \
    "typer>=0.15,<1.0"

# ── Runtime: distroless-ish slim + tini + non-root user ─────────────────────
FROM python:3.12-slim AS runtime

# tini gives us clean SIGTERM handling and reaps zombies. curl for healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini curl \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1001 tiger \
 && useradd  --system --uid 1001 --gid tiger --home /app tiger

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels \
      fastapi "uvicorn[standard]" pydantic pyyaml typer \
 && rm -rf /wheels

# Copy only the tigerline package — nothing from restaurant_api / devswarm.
COPY tigerline /app/tigerline

RUN chown -R tiger:tiger /app
USER tiger

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "uvicorn tigerline.web:app --host 0.0.0.0 --port ${PORT}"]
