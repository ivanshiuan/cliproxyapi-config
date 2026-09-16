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
from ..services import event_hub


async def get_db() -> AsyncIterator[AsyncSession]:
    """Async DB session with commit-on-success / rollback-on-error.

    Services only ``flush()`` (so they can use the returned PK before commit);
    the request-level transaction boundary lives here. If the handler
    returns successfully, we commit; if it raised, we roll back.
    Tests override this with the savepoint-rolled-back fixture so this
    commit never reaches the DB during pytest.

    Real-time (P1.5): a per-request buffer collects floor events recorded by
    services; we publish them to the WebSocket hub only *after* a successful
    commit, so a rolled-back request never leaks a phantom event to tablets.
    """
    token = event_hub.begin_request_buffer()
    try:
        async for session in _get_session():
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()
                for ev in event_hub.drain_pending():
                    event_hub.get_hub().publish(uuid.UUID(ev["store_id"]), ev)
    finally:
        event_hub.reset_request_buffer(token)


def get_line_messenger() -> LineMessenger:
    """LINE messenger DI seam — tests can override with their own stub."""
    return _get_messenger()


def get_line_channel_secret() -> str:
    """HMAC key for inbound LINE webhook signature verification.

    Empty until ``LINE_CHANNEL_SECRET`` is configured, in which case the
    webhook refuses requests (503) rather than accept unverifiable events.
    """
    return get_settings().line_channel_secret


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
    "get_line_channel_secret",
    "get_line_messenger",
]
