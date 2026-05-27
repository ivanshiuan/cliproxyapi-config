"""FastAPI app entrypoint.

Minimal in Phase 0 — exposes /health (liveness + DB ping) and /version.
Endpoint surface grows as DevSwarm generates routers for each module.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from . import __version__
from .config import get_settings
from .database import dispose_engine, ping_db

logger = logging.getLogger("restaurant_api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("restaurant_api starting (env=%s)", settings.env)
    yield
    logger.info("restaurant_api shutting down")
    await dispose_engine()


app = FastAPI(
    title="Taiwan AI Restaurant SaaS — Backend",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/version", tags=["meta"])
async def version() -> dict[str, str]:
    """App version + build info."""
    return {"version": __version__, "name": get_settings().app_name}


@app.get("/health", tags=["meta"])
async def health() -> Response:
    """Liveness + DB ping. Returns 200 on full health, 503 if DB unreachable."""
    db: dict[str, object]
    status_ok = True
    try:
        db = await ping_db()
    except Exception as e:
        status_ok = False
        db = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    payload: dict[str, object] = {
        "status": "ok" if status_ok else "degraded",
        "service": "restaurant_api",
        "version": __version__,
        "checks": {"database": db},
    }
    return JSONResponse(payload, status_code=200 if status_ok else 503)


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "service": "restaurant_api",
        "version": __version__,
        "docs": "/docs",
    }
