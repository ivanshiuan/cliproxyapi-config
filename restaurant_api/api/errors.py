"""Shared error response shape + domain exceptions.

All routers use these so the API surface returns a consistent JSON shape for
errors — no mix of FastAPI default, ValueError text, or stack traces.

    { "error": { "code": "ORDER_ALREADY_CLOSED", "message": "...", "details": {...} } }
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


class ErrorBody(BaseModel):
    """The inner ``error`` object."""

    model_config = ConfigDict(frozen=True)
    code: str = Field(description="Stable error code for clients to switch on")
    message: str = Field(description="Human-readable message; not for parsing")
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Top-level error envelope. Returned for any 4xx/5xx where we control the body."""

    model_config = ConfigDict(frozen=True)
    error: ErrorBody


# ──────────────────────────────────────────────────────────────────────────
# Domain exceptions — business code raises these; the handler converts to JSON
# ──────────────────────────────────────────────────────────────────────────


class DomainError(HTTPException):
    """Base for any expected business-logic error.

    Subclasses set ``status_code`` and ``code``; ``message`` and ``details``
    come at raise time.
    """

    code: str = "DOMAIN_ERROR"
    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        body = ErrorBody(code=self.code, message=message, details=details).model_dump()
        super().__init__(status_code=self.status_code, detail=body)


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(DomainError):
    code = "CONFLICT"
    status_code = status.HTTP_409_CONFLICT


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"
    # Starlette deprecated HTTP_422_UNPROCESSABLE_ENTITY in favour of the RFC
    # 9110 spelling. Hard-coded so this module doesn't break if the alias goes.
    status_code = 422


class UpstreamError(DomainError):
    """A third-party API (e.g. LINE) rejected or failed a request we made to it."""

    code = "UPSTREAM_ERROR"
    status_code = status.HTTP_502_BAD_GATEWAY


# ──────────────────────────────────────────────────────────────────────────
# Exception handlers — attach to the FastAPI app
# ──────────────────────────────────────────────────────────────────────────


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    """Wrap a DomainError's detail (already an ErrorBody dict) in the envelope."""
    body = exc.detail if isinstance(exc.detail, dict) else {
        "code": "INTERNAL", "message": str(exc.detail), "details": None,
    }
    return JSONResponse(status_code=exc.status_code, content={"error": body})


__all__ = [
    "ConflictError",
    "DomainError",
    "ErrorBody",
    "ErrorResponse",
    "NotFoundError",
    "UpstreamError",
    "ValidationError",
    "domain_error_handler",
]
