"""FastAPI app entrypoint.

Production-ready surface:
  - structured-JSON logging with request_id correlation
  - K8s-grade /health/live + /health/ready split
  - graceful drain on shutdown (503 from /health/live while LB drains)
  - DomainError → ErrorResponse envelope for every 4xx/5xx
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .api import health as health_router
from .api.errors import DomainError, domain_error_handler
from .config import get_settings
from .database import dispose_engine
from .middleware import RequestContextMiddleware, configure_logging
from .routers import clock as clock_router
from .routers import events as events_router
from .routers import kitchen as kitchen_router
from .routers import orders as orders_router
from .routers import reservations as reservations_router
from .routers import stock as stock_router
from .services.holiday_calendar import refresh_singleton as refresh_holiday_cache

logger = logging.getLogger("restaurant_api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    logger.info(
        "restaurant_api.starting",
        extra={"env": settings.env, "version": __version__},
    )
    # Prime the in-memory Taiwan holiday cache. Failure here is non-fatal —
    # the calendar falls back to weekend-only mode and the warning surfaces
    # in the /health/ready check downstream.
    try:
        n = await refresh_holiday_cache()
        logger.info("holiday_cache.primed", extra={"holiday_count": n})
    except Exception as e:
        logger.warning(
            "holiday_cache.prime_failed",
            extra={"error": str(e), "fallback": "weekend_only"},
        )
    yield
    health_router.mark_shutting_down()
    logger.info("restaurant_api.shutting_down")
    await dispose_engine()


app = FastAPI(
    title="Taiwan AI Restaurant SaaS — Backend",
    version=__version__,
    lifespan=lifespan,
)

# Request-context middleware FIRST so every downstream log line is tagged.
app.add_middleware(RequestContextMiddleware)

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
_mount_router(kitchen_router, "/kitchen")
# Reservations module exports two routers (one per prefix); mount each with
# the path-based idempotency guard.
if not any(getattr(r, "path", "").startswith("/reservations") for r in app.routes):
    app.include_router(reservations_router.reservations_router)
if not any(getattr(r, "path", "").startswith("/queue") for r in app.routes):
    app.include_router(reservations_router.queue_router)
app.include_router(health_router.router)


@app.get("/version", tags=["meta"])
async def version() -> dict[str, str]:
    """App version + build info."""
    return {"version": __version__, "name": get_settings().app_name}


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "service": "restaurant_api",
        "version": __version__,
        "docs": "/docs",
    }
