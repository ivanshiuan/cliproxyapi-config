"""FastAPI dependency injection helpers shared across routers.

These let business code declare what it needs (DB session, current tenant,
messenger) without each router file repeating the wiring.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session as _get_session
from ..integrations.line import LineMessenger
from ..integrations.line import get_messenger as _get_messenger


async def get_db() -> AsyncIterator[AsyncSession]:
    """Re-exported under a router-friendly name."""
    async for session in _get_session():
        yield session


def get_line_messenger() -> LineMessenger:
    """LINE messenger DI seam — tests can override with their own stub."""
    return _get_messenger()


def get_current_tenant_id(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> uuid.UUID:
    """Resolve the tenant for this request.

    Phase 1 (multi-tenant disabled): falls back to ``Settings.default_tenant_id``.
    Phase 2 (auth on): the X-Tenant-Id header (or JWT claim) drives this.
    """
    settings = get_settings()
    raw = x_tenant_id if (settings.multi_tenant_enabled and x_tenant_id) else settings.default_tenant_id
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tenant id: {raw!r}",
        ) from exc


# Convenience aliases for cleaner router signatures
DbSession = Annotated[AsyncSession, Depends(get_db)]
Messenger = Annotated[LineMessenger, Depends(get_line_messenger)]
TenantId = Annotated[uuid.UUID, Depends(get_current_tenant_id)]


__all__ = [
    "DbSession",
    "Messenger",
    "TenantId",
    "get_current_tenant_id",
    "get_db",
    "get_line_messenger",
]
