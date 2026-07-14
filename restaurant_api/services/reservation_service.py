"""Reservation + walk-in-queue business logic.

Two state machines, one module. Every status transition writes an
``audit_log`` row so the front-of-house team can reconstruct the day
("Why was 王先生's 6:30 booking marked no_show? Who marked the queue
entry abandoned?").

Boundary rules (same pattern as the other services in this repo):
- ``flush()`` only, never ``commit()`` — commit happens in the
  ``api/deps.get_db`` dependency.
- All domain errors raise ``DomainError`` subclasses; the router does
  not translate exceptions.
- ``tenant_id`` is plumbed in by the router from the request DI seam.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..integrations.line import LineMessage, LineMessenger
from ..models import (
    Customer,
    Ingredient,
    MenuItem,
    Order,
    OrderChannel,
    OrderStatus,
    OrderType,
    QueueStatus,
    Reservation,
    ReservationStatus,
    WalkInQueueEntry,
)
from ..schemas.orders import OrderLineCreate, OrderResponse
from ..schemas.reservations import (
    QUEUE_TRANSITIONS,
    RESERVATION_TRANSITIONS,
    QueueEntryResponse,
    QueueJoinRequest,
    QueuePreorderLineRequest,
    QueueSeatRequest,
    QueueSeatResult,
    QueueStatusPatch,
    ReservationCreate,
    ReservationResponse,
    ReservationStatusPatch,
)
from ..schemas.tables import TableSessionOpen
from . import orders_service, pos_order_service, table_service
from .audit_service import audit

logger = logging.getLogger("restaurant_api.integrations.line")
_TPE = ZoneInfo("Asia/Taipei")


async def _notify_customer(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID | None,
    tenant_id: uuid.UUID,
    text: str,
    messenger: LineMessenger | None,
) -> None:
    """Fire-and-forget LINE push tied to a reservation / queue status change.

    No-op when there's no messenger, no linked customer, or that customer
    isn't a LINE user (front-of-house matched them by phone). A LINE failure
    is logged but never blocks the status transition — same contract as the
    order-close receipt push.
    """
    if messenger is None or customer_id is None:
        return
    customer = (
        await session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if customer is None or not customer.line_user_id:
        return
    try:
        await messenger.push(customer.line_user_id, LineMessage(kind="text", text=text))
    except Exception as exc:
        logger.warning(
            "reservation.line_push_failed customer_id=%s err=%s", customer_id, exc
        )

# ──────────────────────────────────────────────────────────────────────────
# Reservations
# ──────────────────────────────────────────────────────────────────────────


async def create_reservation(
    session: AsyncSession,
    payload: ReservationCreate,
    *,
    tenant_id: uuid.UUID,
) -> ReservationResponse:
    row = Reservation(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        customer_id=payload.customer_id,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        party_size=payload.party_size,
        reserved_for=payload.reserved_for,
        duration_minutes=payload.duration_minutes,
        source=payload.source,
        deposit_amount=payload.deposit_amount,
        notes=payload.notes,
        # status / arrived_at fall back to model defaults.
    )
    session.add(row)
    await session.flush()

    await audit(
        session,
        action="reservation.created",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        target=("reservations", row.id),
        after={
            "party_size": payload.party_size,
            "reserved_for": payload.reserved_for.isoformat(),
            "source": payload.source,
            "status": row.status.value,
        },
    )
    return ReservationResponse.model_validate(row)


async def get_reservation(
    session: AsyncSession,
    reservation_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> ReservationResponse:
    row = await _load_reservation(session, reservation_id, tenant_id)
    return ReservationResponse.model_validate(row)


async def list_reservations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    status: ReservationStatus | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    limit: int = 200,
) -> list[ReservationResponse]:
    stmt = select(Reservation).where(Reservation.tenant_id == tenant_id)
    if store_id is not None:
        stmt = stmt.where(Reservation.store_id == store_id)
    if status is not None:
        stmt = stmt.where(Reservation.status == status)
    if from_dt is not None:
        stmt = stmt.where(Reservation.reserved_for >= from_dt)
    if to_dt is not None:
        stmt = stmt.where(Reservation.reserved_for <= to_dt)
    stmt = stmt.order_by(Reservation.reserved_for).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [ReservationResponse.model_validate(r) for r in rows]


async def patch_reservation_status(
    session: AsyncSession,
    reservation_id: uuid.UUID,
    payload: ReservationStatusPatch,
    *,
    tenant_id: uuid.UUID,
    messenger: LineMessenger | None = None,
) -> ReservationResponse:
    row = await _load_reservation(session, reservation_id, tenant_id)
    _check_reservation_transition(row.status, payload.status)

    before_status = row.status
    row.status = payload.status

    # arrived_at semantics: when transitioning into SEATED, record the
    # arrival timestamp. Caller can pass an explicit value (host noted
    # they walked in 5 min ago); default to now.
    if payload.status == ReservationStatus.SEATED:
        row.arrived_at = payload.arrived_at or datetime.now(UTC)
    elif payload.status == ReservationStatus.NO_SHOW and row.arrived_at is None:
        # Stamp no_show events too so analytics can compute the gap between
        # ``reserved_for`` and the no-show call.
        row.arrived_at = payload.arrived_at or datetime.now(UTC)

    await session.flush()
    # Pull the server-computed updated_at back so model_validate can read
    # it without triggering a lazy-load (forbidden in async sessions).
    await session.refresh(row)

    await audit(
        session,
        action=f"reservation.{payload.status.value}",
        tenant_id=tenant_id,
        store_id=row.store_id,
        actor_id=payload.actor_id,
        target=("reservations", row.id),
        before={"status": before_status.value},
        after={"status": payload.status.value},
        reason=payload.reason,
    )

    # Notify the guest when the booking is confirmed. ASCII punctuation only
    # (RUF002 flags full-width marks in source strings).
    if payload.status == ReservationStatus.CONFIRMED:
        local = row.reserved_for.astimezone(_TPE)
        await _notify_customer(
            session,
            customer_id=row.customer_id,
            tenant_id=tenant_id,
            text=(
                f"[訂位確認] {row.contact_name} 您好,\n"
                f"您 {row.party_size} 位的訂位已確認.\n"
                f"時間: {local:%Y-%m-%d %H:%M}\n"
                f"期待您的光臨!"
            ),
            messenger=messenger,
        )

    return ReservationResponse.model_validate(row)


# ──────────────────────────────────────────────────────────────────────────
# Walk-in queue
# ──────────────────────────────────────────────────────────────────────────


async def join_queue(
    session: AsyncSession,
    payload: QueueJoinRequest,
    *,
    tenant_id: uuid.UUID,
) -> QueueEntryResponse:
    row = WalkInQueueEntry(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        customer_id=payload.customer_id,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        party_size=payload.party_size,
        queue_no=payload.queue_no,
        notes=payload.notes,
    )
    session.add(row)
    await session.flush()

    await audit(
        session,
        action="queue.joined",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        target=("walk_in_queue", row.id),
        after={
            "party_size": payload.party_size,
            "queue_no": payload.queue_no,
        },
    )
    return QueueEntryResponse.model_validate(row)


async def list_queue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    status: QueueStatus | None = None,
    limit: int = 200,
) -> list[QueueEntryResponse]:
    stmt = select(WalkInQueueEntry).where(WalkInQueueEntry.tenant_id == tenant_id)
    if store_id is not None:
        stmt = stmt.where(WalkInQueueEntry.store_id == store_id)
    if status is not None:
        stmt = stmt.where(WalkInQueueEntry.status == status)
    stmt = stmt.order_by(WalkInQueueEntry.joined_at).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [QueueEntryResponse.model_validate(r) for r in rows]


async def patch_queue_status(
    session: AsyncSession,
    queue_id: uuid.UUID,
    payload: QueueStatusPatch,
    *,
    tenant_id: uuid.UUID,
    messenger: LineMessenger | None = None,
) -> QueueEntryResponse:
    row = await _load_queue_entry(session, queue_id, tenant_id)
    _check_queue_transition(row.status, payload.status)

    before_status = row.status
    row.status = payload.status

    now = datetime.now(UTC)
    if payload.status == QueueStatus.CALLED:
        row.called_at = now
    elif payload.status == QueueStatus.SEATED:
        # Don't blow away a previous called_at if it exists.
        row.seated_at = now

    await session.flush()
    # Pull DB-computed updated_at back so model_validate doesn't lazy-load.
    await session.refresh(row)

    await audit(
        session,
        action=f"queue.{payload.status.value}",
        tenant_id=tenant_id,
        store_id=row.store_id,
        actor_id=payload.actor_id,
        target=("walk_in_queue", row.id),
        before={"status": before_status.value},
        after={"status": payload.status.value},
        reason=payload.reason,
    )

    # Notify the guest when their table is called. ASCII punctuation only.
    if payload.status == QueueStatus.CALLED:
        label = f" (號碼 {row.queue_no})" if row.queue_no else ""
        await _notify_customer(
            session,
            customer_id=row.customer_id,
            tenant_id=tenant_id,
            text=(
                f"[候位通知] {row.contact_name} 您好,\n"
                f"您的桌位已準備好{label},\n"
                f"請於 5 分鐘內至櫃台報到, 謝謝!"
            ),
            messenger=messenger,
        )

    return QueueEntryResponse.model_validate(row)


# ──────────────────────────────────────────────────────────────────────────
# 候位先點餐 — pre-order while waiting, seat carries it onto the table
# ──────────────────────────────────────────────────────────────────────────


async def add_queue_preorder_line(
    session: AsyncSession,
    queue_id: uuid.UUID,
    payload: QueuePreorderLineRequest,
    *,
    tenant_id: uuid.UUID,
) -> OrderResponse:
    """Add one item to a waiting party's pre-order.

    Creates the party's ``Order`` on first call — ``table_session_id`` stays
    NULL while they wait. On seating (``seat_queue_entry``), this order gets
    reparented onto the newly opened session with a single FK flip (no line
    movement needed, unlike 併桌 which merges two already-seated bills).
    """
    row = await _load_queue_entry(session, queue_id, tenant_id)
    if row.status not in (QueueStatus.WAITING, QueueStatus.CALLED):
        raise ConflictError(
            message=f"queue entry is {row.status.value}, cannot pre-order",
            details={"current": row.status.value},
        )

    if row.order_id is None:
        order = Order(
            tenant_id=tenant_id,
            store_id=row.store_id,
            order_no=f"Q-{row.id.hex[:12]}",
            business_date=datetime.now(_TPE).date(),
            status=OrderStatus.OPEN,
            order_type=OrderType.DINE_IN,
            channel=OrderChannel.POS,
            customer_id=row.customer_id,
            notes=f"候位先點餐 — {row.contact_name}" + (f" ({row.queue_no})" if row.queue_no else ""),
        )
        session.add(order)
        await session.flush()
        row.order_id = order.id
        await session.flush()
    else:
        order = await session.get(Order, row.order_id)
        if order is None or order.status != OrderStatus.OPEN:  # pragma: no cover - FK guarantees presence
            raise ConflictError(message="pre-order is no longer open", details={"order_id": str(row.order_id)})

    item = (
        await session.execute(
            select(MenuItem).where(
                MenuItem.id == payload.menu_item_id,
                MenuItem.tenant_id == tenant_id,
                MenuItem.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError(
            message=f"menu item {payload.menu_item_id} not found",
            details={"menu_item_id": str(payload.menu_item_id)},
        )
    pos_order_service._guard_available_now(item)

    now = datetime.now(UTC)
    placeholder: dict[str, Ingredient] = {}

    async def _get_placeholder() -> Ingredient:
        if "ing" not in placeholder:
            placeholder["ing"] = await orders_service._placeholder_ingredient(session, tenant_id, row.store_id)
        return placeholder["ing"]

    await orders_service._add_line_with_movement(
        session=session,
        order=order,
        tenant_id=tenant_id,
        store_id=row.store_id,
        line_payload=OrderLineCreate(
            menu_item_id=payload.menu_item_id,
            qty=payload.qty,
            unit_price=item.price,
            notes=payload.notes,
            kitchen_station=None,  # not sent to KDS until the party is seated
        ),
        get_placeholder=_get_placeholder,
        occurred_at=now,
    )
    await audit(
        session,
        action="queue.preorder_line_added",
        tenant_id=tenant_id,
        store_id=row.store_id,
        actor_id=payload.actor_id,
        target=("orders", order.id),
        after={"menu_item_id": str(payload.menu_item_id), "qty": str(payload.qty)},
    )
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return orders_service.order_to_response(order)


async def seat_queue_entry(
    session: AsyncSession,
    queue_id: uuid.UUID,
    payload: QueueSeatRequest,
    *,
    tenant_id: uuid.UUID,
) -> QueueSeatResult:
    """帶位入座 — open the table, reparent any pre-order onto it, mark seated.

    One transaction: the pre-order (if any) becomes visible on the table's
    bill the instant the session opens — the kitchen doesn't wait for the
    party to re-order at the table.
    """
    row = await _load_queue_entry(session, queue_id, tenant_id)
    if row.status not in (QueueStatus.WAITING, QueueStatus.CALLED):
        raise ConflictError(
            message=f"queue entry is {row.status.value}, cannot seat",
            details={"current": row.status.value},
        )

    ts_response = await table_service.open_session(
        session,
        payload.table_id,
        TableSessionOpen(
            party_size=row.party_size,
            opened_by=payload.actor_id,
            notes=f"候位帶位 — {row.contact_name}" + (f" ({row.queue_no})" if row.queue_no else ""),
        ),
        tenant_id=tenant_id,
    )

    if row.order_id is not None:
        preorder = await session.get(Order, row.order_id)
        if preorder is not None and preorder.status == OrderStatus.OPEN:
            preorder.table_session_id = ts_response.id
            await session.flush()

    before_status = row.status
    row.status = QueueStatus.SEATED
    now = datetime.now(UTC)
    if row.called_at is None:
        row.called_at = now
    row.seated_at = now
    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="queue.seated",
        tenant_id=tenant_id,
        store_id=row.store_id,
        actor_id=payload.actor_id,
        target=("walk_in_queue", row.id),
        before={"status": before_status.value},
        after={"status": "seated", "table_id": str(payload.table_id), "order_id": str(row.order_id) if row.order_id else None},
    )

    return QueueSeatResult(
        queue_entry=QueueEntryResponse.model_validate(row),
        table_session=ts_response,
    )


# ──────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────


async def _load_reservation(
    session: AsyncSession,
    reservation_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Reservation:
    stmt = select(Reservation).where(
        Reservation.id == reservation_id,
        Reservation.tenant_id == tenant_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"reservation {reservation_id} not found",
            details={"reservation_id": str(reservation_id)},
        )
    return row


async def _load_queue_entry(
    session: AsyncSession,
    queue_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> WalkInQueueEntry:
    stmt = select(WalkInQueueEntry).where(
        WalkInQueueEntry.id == queue_id,
        WalkInQueueEntry.tenant_id == tenant_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"queue entry {queue_id} not found",
            details={"queue_id": str(queue_id)},
        )
    return row


def _check_reservation_transition(
    current: ReservationStatus, target: ReservationStatus
) -> None:
    if target == current:
        # Idempotent no-op rejected explicitly — a re-fire with the same
        # status is almost always a client bug worth surfacing.
        raise ConflictError(
            message=f"reservation already in status {current.value}",
            details={"current": current.value, "target": target.value},
        )
    allowed = RESERVATION_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ConflictError(
            message=(
                f"cannot transition reservation from {current.value} "
                f"to {target.value}"
            ),
            details={
                "current": current.value,
                "target": target.value,
                "allowed": sorted(s.value for s in allowed),
            },
        )


def _check_queue_transition(current: QueueStatus, target: QueueStatus) -> None:
    if target == current:
        raise ConflictError(
            message=f"queue entry already in status {current.value}",
            details={"current": current.value, "target": target.value},
        )
    allowed = QUEUE_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ConflictError(
            message=(
                f"cannot transition queue entry from {current.value} "
                f"to {target.value}"
            ),
            details={
                "current": current.value,
                "target": target.value,
                "allowed": sorted(s.value for s in allowed),
            },
        )


__all__ = [
    "add_queue_preorder_line",
    "create_reservation",
    "get_reservation",
    "join_queue",
    "list_queue",
    "list_reservations",
    "patch_queue_status",
    "patch_reservation_status",
    "seat_queue_entry",
]
