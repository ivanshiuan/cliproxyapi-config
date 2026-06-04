"""Audit logging service — one-line API so service code never forgets to write
the audit trail.

Usage:

    from restaurant_api.services.audit_service import audit

    await audit(
        session,
        action="discount.applied",
        target=("orders", order.id),
        actor_id=current_employee.id,
        after={"kind": "comp", "value": "100.00", "reason": "VIP招待"},
        reason="店長手動招待",
    )

Pulls ``request_id`` from the contextvar set by ``RequestContextMiddleware``
so every audit row joins the same correlation chain as the access log.

The underlying ``audit_log`` table is append-only at the DB level (INSTEAD
NOTHING rule from Phase-1 closed-loop migration). This service is the only
sanctioned write path — do not insert AuditLog rows directly elsewhere.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..middleware import get_context_tenant_id, get_request_id
from ..models import AuditLog

logger = logging.getLogger("restaurant_api.audit")


async def audit(
    session: AsyncSession,
    *,
    action: str,
    tenant_id: uuid.UUID,
    target: tuple[str, uuid.UUID] | None = None,
    actor_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """Write one audit_log row. Returns the persisted instance (not committed).

    ``action`` uses dotted namespace: ``<entity>.<verb>``
      examples: ``discount.applied``, ``comp.granted``, ``stock.adjusted``,
                ``order.voided``, ``cash_drawer.closed``,
                ``employee.role_changed``, ``invoice.allowance_issued``

    ``target`` is the (table_name, row_id) pair the action affected. Optional
    for tenant-level events (settings change, role grant).

    ``before`` / ``after`` are JSON-serializable diff snapshots; only one may
    be null for pure-create or pure-delete events.

    Caller is responsible for the surrounding transaction — this method
    flushes but does not commit (matches every other service in the project).
    """
    target_table = target[0] if target else None
    target_id = target[1] if target else None

    row = AuditLog(
        tenant_id=tenant_id,
        store_id=store_id,
        actor_id=actor_id,
        action=action,
        target_table=target_table,
        target_id=target_id,
        before=before,
        after=after,
        reason=reason,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(row)
    await session.flush()

    logger.info(
        "audit.write",
        extra={
            "action": action,
            "target_table": target_table,
            "target_id": str(target_id) if target_id else None,
            "actor_id": str(actor_id) if actor_id else None,
            "audit_id": str(row.id),
            "request_id": get_request_id(),
            "tenant_id": get_context_tenant_id() or str(tenant_id),
        },
    )
    return row


async def audit_from_request(
    session: AsyncSession,
    *,
    action: str,
    tenant_id: uuid.UUID,
    target: tuple[str, uuid.UUID] | None = None,
    actor_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    request: Any = None,
) -> AuditLog:
    """Convenience wrapper that pulls ip/user-agent from a FastAPI Request."""
    ip = None
    ua = None
    if request is not None:
        client = getattr(request, "client", None)
        if client and getattr(client, "host", None):
            ip = client.host
        ua = request.headers.get("User-Agent")
    return await audit(
        session,
        action=action,
        tenant_id=tenant_id,
        target=target,
        actor_id=actor_id,
        store_id=store_id,
        before=before,
        after=after,
        reason=reason,
        ip_address=ip,
        user_agent=ua,
    )
