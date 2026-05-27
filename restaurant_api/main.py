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
from .api.errors import DomainError, domain_error_handler
from .config import get_settings
from .database import dispose_engine, ping_db
from .routers import clock as clock_router
from .routers import events as events_router
from .routers import orders as orders_router
from .routers import stock as stock_router

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

# Domain-error → JSON envelope handler (single shape for every 4xx/5xx).
app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]


# Mount routers. Each router module's tests also self-mount with a guard,
# so the import order doesn't matter — mounting here is the "real prod"
# wiring; the in-test self-mount becomes a no-op once we're already here.
def _mount_router(module, prefix_hint: str) -> None:
    """Idempotent include_router — skip if any route under prefix already exists."""
    if any(getattr(r, "path", "").startswith(prefix_hint) for r in app.routes):
        return
    app.include_router(module.router)


_mount_router(orders_router, "/orders")
_mount_router(stock_router, "/stock")
_mount_router(clock_router, "/clock")
_mount_router(events_router, "/events")


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
