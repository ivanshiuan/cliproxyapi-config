"""KDS (Kitchen Display System) business logic.

Operates on ``order_lines`` rows that have been opted into KDS
(``kitchen_station IS NOT NULL``). The orders service stamps that field
at line creation time; this service progresses the line through the
``KitchenStatus`` lifecycle.

Boundary rules (same as every service in the project):
- ``flush()`` only, never ``commit()``.
- ``tenant_id`` is plumbed in by the router DI seam — services trust it.
- All errors raise ``DomainError`` subclasses.

P3 additions: the queue rows carry ``item_name`` / ``table_name`` (joined
server-side so the KDS screen renders without extra fetches), and every status
transition also lands on the real-time floor stream (``kitchen.status_changed``)
so KDS screens and POS floors sync live over the same P1.5 channel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models import (
    DiningTable,
    KitchenStation,
    KitchenStatus,
    MenuItem,
    Order,
    OrderLine,
    TableSession,
)
from ..schemas.kitchen import (
    KITCHEN_TRANSITIONS,
    KitchenLineResponse,
    KitchenStatusPatch,
)
from .audit_service import audit
from .event_hub import record_event


def _base_stmt():
    """order_lines + orders + (optional) item / table names in one round trip."""
    return (
        select(OrderLine, Order, MenuItem.name, DiningTable.name)
        .join(Order, Order.id == OrderLine.order_id)
        .outerjoin(MenuItem, MenuItem.id == OrderLine.menu_item_id)
        .outerjoin(TableSession, TableSession.id == Order.table_session_id)
        .outerjoin(DiningTable, DiningTable.id == TableSession.table_id)
    )


async def list_queue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    station: KitchenStation | None = None,
    status: KitchenStatus | None = None,
    limit: int = 200,
) -> list[KitchenLineResponse]:
    """List lines on the KDS, oldest first.

    Default filter: ``kitchen_station IS NOT NULL`` (excludes counter-only
    lines). Pass ``status=QUEUED`` to get only what's waiting to cook.
    """
    stmt = (
        _base_stmt()
        .where(OrderLine.tenant_id == tenant_id)
        .where(OrderLine.kitchen_station.is_not(None))
    )
    if store_id is not None:
        stmt = stmt.where(Order.store_id == store_id)
    if station is not None:
        stmt = stmt.where(OrderLine.kitchen_station == station)
    if status is not None:
        stmt = stmt.where(OrderLine.kitchen_status == status)
    stmt = stmt.order_by(OrderLine.sent_to_kitchen_at).limit(limit)

    rows = (await session.execute(stmt)).all()
    return [
        _project(line, order, item_name=item_name, table_name=table_name)
        for line, order, item_name, table_name in rows
    ]


async def patch_line_status(
    session: AsyncSession,
    line_id: uuid.UUID,
    payload: KitchenStatusPatch,
    *,
    tenant_id: uuid.UUID,
) -> KitchenLineResponse:
    """Transition a single KDS line. Stamps the appropriate timestamp
    column on the way in (``cooking_started_at`` / ``ready_at`` /
    ``served_at``)."""
    stmt = (
        _base_stmt()
        .where(OrderLine.id == line_id)
        .where(OrderLine.tenant_id == tenant_id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"kitchen line {line_id} not found",
            details={"line_id": str(line_id)},
        )
    line, order, item_name, table_name = row

    if line.kitchen_station is None or line.kitchen_status is None:
        raise ValidationError(
            message="line was not routed to a kitchen station",
            details={"line_id": str(line_id)},
        )

    _check_transition(line.kitchen_status, payload.status)

    before_status = line.kitchen_status
    line.kitchen_status = payload.status

    now = datetime.now(UTC)
    if payload.status == KitchenStatus.COOKING:
        line.cooking_started_at = now
    elif payload.status == KitchenStatus.READY:
        line.ready_at = now
    elif payload.status == KitchenStatus.SERVED:
        line.served_at = now

    await session.flush()
    await session.refresh(line)

    await audit(
        session,
        action=f"kitchen.{payload.status.value}",
        tenant_id=tenant_id,
        store_id=order.store_id,
        actor_id=payload.actor_id,
        target=("order_lines", line.id),
        before={"kitchen_status": before_status.value},
        after={"kitchen_status": payload.status.value},
        reason=payload.reason,
    )
    # Live push (P1.5 channel): KDS screens re-pull their board, POS floors can
    # surface 可出餐 state — one stream for the whole floor.
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=order.store_id,
        event_type="kitchen.status_changed",
        session_id=order.table_session_id,
        payload={
            "line_id": str(line.id),
            "order_id": str(order.id),
            "status": payload.status.value,
        },
    )
    return _project(line, order, item_name=item_name, table_name=table_name)


# ──────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────


def _project(
    line: OrderLine,
    order: Order,
    *,
    item_name: str | None = None,
    table_name: str | None = None,
) -> KitchenLineResponse:
    return KitchenLineResponse.model_validate(
        {
            "line_id": line.id,
            "order_id": order.id,
            "order_no": order.order_no,
            "store_id": order.store_id,
            "business_date": order.business_date.isoformat(),
            "menu_item_id": line.menu_item_id,
            "item_name": item_name,
            "table_name": table_name,
            "qty": float(line.qty),
            "notes": line.notes,
            "kitchen_station": line.kitchen_station,
            "kitchen_status": line.kitchen_status,
            "sent_to_kitchen_at": line.sent_to_kitchen_at,
            "cooking_started_at": line.cooking_started_at,
            "ready_at": line.ready_at,
            "served_at": line.served_at,
        }
    )


def _check_transition(current: KitchenStatus, target: KitchenStatus) -> None:
    if target == current:
        raise ConflictError(
            message=f"line already in status {current.value}",
            details={"current": current.value, "target": target.value},
        )
    allowed = KITCHEN_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ConflictError(
            message=(
                f"cannot transition KDS line from {current.value} to "
                f"{target.value}"
            ),
            details={
                "current": current.value,
                "target": target.value,
                "allowed": sorted(s.value for s in allowed),
            },
        )


__all__ = ["list_queue", "patch_line_status"]
