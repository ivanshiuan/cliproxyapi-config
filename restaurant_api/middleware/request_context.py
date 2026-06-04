"""Request-context middleware: stamps every request with an id, exposes it via
contextvar so downstream log records / service code / LINE notifications can
join the same correlation chain.

Flow:
    Client → X-Request-Id (or we mint a UUIDv7)
           → contextvar request_id_var set
           → response Header X-Request-Id echoed
           → every log record gets `request_id` field via the LogFilter
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..models.base import uuid7

# ──────────────────────────────────────────────────────────────────────────
# Context vars — picked up by RequestIdLogFilter
# ──────────────────────────────────────────────────────────────────────────

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_TENANT_ID: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


def set_request_id(value: str | None) -> None:
    _REQUEST_ID.set(value)


def get_context_tenant_id() -> str | None:
    return _TENANT_ID.get()


def set_context_tenant_id(value: str | None) -> None:
    _TENANT_ID.set(value)


# ──────────────────────────────────────────────────────────────────────────
# Middleware
# ──────────────────────────────────────────────────────────────────────────


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Stamps request_id + duration on every response and into the log context."""

    HEADER = "X-Request-Id"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(self.HEADER)
        request_id = incoming or str(uuid7())
        _REQUEST_ID.set(request_id)
        # Tenant header is also picked up here so log records see it without
        # depending on FastAPI DI ordering.
        _TENANT_ID.set(request.headers.get("X-Tenant-Id"))

        start = time.perf_counter()
        logger = logging.getLogger("restaurant_api.access")
        logger.info(
            "http.request.start",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query": str(request.url.query) or None,
            },
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "http.request.error",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 1),
                },
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        response.headers[self.HEADER] = request_id
        logger.info(
            "http.request.complete",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 1),
            },
        )
        return response
