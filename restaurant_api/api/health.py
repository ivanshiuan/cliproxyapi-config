"""K8s-grade health endpoints — separated so liveness probes never restart a
pod just because the DB is briefly down.

  /health/live    — process is alive. Returns 200 unless we're shutting down.
                    Used by liveness probes (kill the pod if this fails).

  /health/ready   — process can serve traffic. DB + every external dep must
                    be reachable. Used by readiness probes (drain from LB if
                    this fails, but DO NOT restart the pod).

  /health         — backward-compat composite (200 only if both above OK).
                    What humans curl from a terminal.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import __version__
from ..config import get_settings
from ..database import ping_db
from .errors import AuthError

logger = logging.getLogger("restaurant_api.health")

router = APIRouter(prefix="/health", tags=["meta"])


def require_internal_probe(
    x_internal_probe: Annotated[str | None, Header(alias="X-Internal-Probe")] = None,
) -> None:
    """Gate for /health/ready — only internal probes may see the DB status.

    Uses ``session_secret`` (already required in prod) as the shared token
    so no new setting to remember. Compare with constant-time hmac.compare_digest.
    """
    settings = get_settings()
    if settings.env == "dev":
        # In dev, the probe header is optional so ``curl /health/ready``
        # from a laptop still works during troubleshooting.
        return
    expected = settings.session_secret
    if not x_internal_probe or not hmac.compare_digest(x_internal_probe, expected):
        raise AuthError("readiness probe requires X-Internal-Probe header")


class LivenessResponse(BaseModel):
    status: Literal["alive"]
    service: str
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    service: str
    version: str
    checks: dict[str, dict[str, object]]


_SHUTTING_DOWN = False


def mark_shutting_down() -> None:
    """Called from FastAPI lifespan on shutdown so /health/live starts
    returning 503 — gives the LB time to drain before SIGKILL."""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    logger.info("health.shutdown_signal_received")


@router.get(
    "/live",
    response_model=LivenessResponse,
    responses={503: {"description": "Process is shutting down"}},
)
async def live() -> Response:
    """Liveness — answers `is this process worth keeping alive?`"""
    if _SHUTTING_DOWN:
        return JSONResponse(
            {"status": "draining", "service": "restaurant_api", "version": __version__},
            status_code=503,
        )
    return JSONResponse(
        {"status": "alive", "service": "restaurant_api", "version": __version__},
        status_code=200,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"description": "One or more dependencies unavailable"}},
    dependencies=[Depends(require_internal_probe)],
)
async def ready() -> Response:
    """Readiness — answers `can this process serve traffic right now?`"""
    checks: dict[str, dict[str, object]] = {}
    all_ok = True

    try:
        db_status = await ping_db()
        checks["database"] = db_status
        if not db_status.get("ok"):
            all_ok = False
    except Exception as e:
        checks["database"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        all_ok = False

    if _SHUTTING_DOWN:
        all_ok = False
        checks["lifecycle"] = {"ok": False, "reason": "shutting_down"}

    body = {
        "status": "ready" if all_ok else "degraded",
        "service": "restaurant_api",
        "version": __version__,
        "checks": checks,
    }
    return JSONResponse(body, status_code=200 if all_ok else 503)


@router.get("", include_in_schema=False)
async def composite() -> Response:
    """Backward-compat composite for humans curl-ing the old /health."""
    return await ready()
