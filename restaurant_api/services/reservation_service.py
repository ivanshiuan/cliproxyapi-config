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

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..models import (
    QueueStatus,
    Reservation,
    ReservationStatus,
    WalkInQueueEntry,
)
from ..schemas.reservations import (
    QUEUE_TRANSITIONS,
    RESERVATION_TRANSITIONS,
    QueueEntryResponse,
    QueueJoinRequest,
    QueueStatusPatch,
    ReservationCreate,
    ReservationResponse,
    ReservationStatusPatch,
)
from .audit_service import audit

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
    return QueueEntryResponse.model_validate(row)


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
    "create_reservation",
    "get_reservation",
    "join_queue",
    "list_queue",
    "list_reservations",
    "patch_queue_status",
    "patch_reservation_status",
]
