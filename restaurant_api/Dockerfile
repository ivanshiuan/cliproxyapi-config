# ──────────────────────────────────────────────────────────────────────────
# Restaurant API — multi-stage production image
#
# Stage 1 (builder): full toolchain, wheel cache, model migrations check
# Stage 2 (runtime): slim image, non-root user, no build tools
#
# Build:    docker build -t resto-api:VERSION -f restaurant_api/Dockerfile .
# Run API:  docker run -p 8000:8000 --env-file .env resto-api:VERSION
# Run Jobs: docker run --env-file .env resto-api:VERSION python -m restaurant_api.jobs
# ──────────────────────────────────────────────────────────────────────────

# ─── builder ────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml requirements.txt ./
COPY README.md ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip wheel \
    && pip install -r requirements.txt

# Copy source (not models in venv site-packages — we want editable-friendly layout)
COPY devswarm /build/devswarm
COPY restaurant_api /build/restaurant_api
COPY scripts /build/scripts
RUN pip install -e /build

# ─── runtime ────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app
# PYTHONPATH=/app: the editable install (pip install -e /build) records the
# source path as /build, but runtime copies source to /app — so point Python at
# /app to keep restaurant_api/devswarm importable regardless of the egg-link.

# libpq for psycopg / asyncpg; tini for proper PID-1 signal handling;
# curl for the container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        tini \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. uid:gid 10001:10001 is the conventional non-system pick.
RUN groupadd --system --gid 10001 resto \
    && useradd  --system --uid 10001 --gid resto --no-create-home --shell /usr/sbin/nologin resto

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build /app
WORKDIR /app

# Drop privileges; readonly app dir would be ideal but Alembic writes
# version-history under restaurant_api/alembic/versions/ if we ever
# autogenerate inside the container — left writeable on purpose.
RUN chown -R resto:resto /app \
    && chmod +x /app/restaurant_api/start.sh
USER resto

EXPOSE 8000

# Container-level healthcheck — Cloudflare/K8s readiness probes use HTTP.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

# tini = PID-1 reaping; start.sh = migrate → seed → uvicorn.
# PORT is injected by Render; defaults to 8000 locally.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/restaurant_api/start.sh"]
