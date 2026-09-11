"""Floor-plan + table-session business logic (POS P1.3).

Pure async (flush-only). Every session transition (open/close/transfer) writes
an ``audit_log`` row. Table status is never stored on the table row — the
floor board derives 空桌/用餐中 from the live open session, so there is no
stale-state bug class.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..models import DiningTable, TableSession, TableSessionStatus
from ..schemas.tables import (
    DiningTableCreate,
    DiningTableResponse,
    DiningTableUpdate,
    FloorTableView,
    TableSessionOpen,
    TableSessionResponse,
    TableSessionTransfer,
)
from .audit_service import audit
from .event_hub import record_event

# ──────────────────────────────────────────────────────────────────────────
# Dining tables (CRUD)
# ──────────────────────────────────────────────────────────────────────────


async def create_table(
    session: AsyncSession,
    payload: DiningTableCreate,
    *,
    tenant_id: uuid.UUID,
) -> DiningTableResponse:
    await _guard_table_name_unique(
        session, tenant_id=tenant_id, store_id=payload.store_id, name=payload.name
    )
    row = DiningTable(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        name=payload.name,
        zone=payload.zone,
        seats=payload.seats,
        sort_order=payload.sort_order,
        qr_token=secrets.token_urlsafe(16),
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="dining_table.created",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        target=("dining_tables", row.id),
        after={"name": payload.name, "seats": payload.seats},
    )
    return DiningTableResponse.model_validate(row)


async def list_tables(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
) -> list[DiningTableResponse]:
    stmt = select(DiningTable).where(
        DiningTable.tenant_id == tenant_id,
        DiningTable.deleted_at.is_(None),
    )
    if store_id is not None:
        stmt = stmt.where(DiningTable.store_id == store_id)
    stmt = stmt.order_by(DiningTable.sort_order, DiningTable.name)
    rows = (await session.execute(stmt)).scalars().all()
    return [DiningTableResponse.model_validate(r) for r in rows]


async def update_table(
    session: AsyncSession,
    table_id: uuid.UUID,
    payload: DiningTableUpdate,
    *,
    tenant_id: uuid.UUID,
) -> DiningTableResponse:
    row = await _load_table(session, table_id, tenant_id)
    fields = payload.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] != row.name:
        await _guard_table_name_unique(
            session, tenant_id=tenant_id, store_id=row.store_id, name=fields["name"]
        )
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="dining_table.updated",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("dining_tables", row.id),
        after=fields,
    )
    return DiningTableResponse.model_validate(row)


async def delete_table(
    session: AsyncSession,
    table_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    row = await _load_table(session, table_id, tenant_id)
    # Refuse to delete a table with a live seating — close it first.
    if await _open_session_for_table(session, row.id) is not None:
        raise ConflictError(
            message="cannot delete a table with an open session",
            details={"table_id": str(table_id)},
        )
    row.deleted_at = datetime.now(UTC)
    await session.flush()
    await audit(
        session,
        action="dining_table.deleted",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("dining_tables", row.id),
        before={"name": row.name},
    )


# ──────────────────────────────────────────────────────────────────────────
# Floor-plan board
# ──────────────────────────────────────────────────────────────────────────


async def floor_plan(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
) -> list[FloorTableView]:
    """Return every live table for a store with its open session (if any)."""
    tables = (
        await session.execute(
            select(DiningTable)
            .where(
                DiningTable.tenant_id == tenant_id,
                DiningTable.store_id == store_id,
                DiningTable.deleted_at.is_(None),
            )
            .order_by(DiningTable.sort_order, DiningTable.name)
        )
    ).scalars().all()

    open_sessions = (
        await session.execute(
            select(TableSession).where(
                TableSession.tenant_id == tenant_id,
                TableSession.store_id == store_id,
                TableSession.status == TableSessionStatus.OPEN,
            )
        )
    ).scalars().all()
    by_table = {s.table_id: s for s in open_sessions}

    board: list[FloorTableView] = []
    for t in tables:
        sess = by_table.get(t.id)
        board.append(
            FloorTableView(
                id=t.id,
                name=t.name,
                zone=t.zone,
                seats=t.seats,
                sort_order=t.sort_order,
                is_occupied=sess is not None,
                session=(
                    TableSessionResponse.model_validate(sess) if sess is not None else None
                ),
            )
        )
    return board


# ──────────────────────────────────────────────────────────────────────────
# Table sessions (open / close / transfer)
# ──────────────────────────────────────────────────────────────────────────


async def open_session(
    session: AsyncSession,
    table_id: uuid.UUID,
    payload: TableSessionOpen,
    *,
    tenant_id: uuid.UUID,
) -> TableSessionResponse:
    table = await _load_table(session, table_id, tenant_id)
    if await _open_session_for_table(session, table.id) is not None:
        raise ConflictError(
            message="table already has an open session",
            details={"table_id": str(table_id)},
        )
    row = TableSession(
        tenant_id=tenant_id,
        store_id=table.store_id,
        table_id=table.id,
        party_size=payload.party_size,
        opened_by=payload.opened_by,
        reservation_id=payload.reservation_id,
        notes=payload.notes,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="table_session.opened",
        tenant_id=tenant_id,
        store_id=table.store_id,
        actor_id=payload.opened_by,
        target=("table_sessions", row.id),
        after={"table_id": str(table.id), "party_size": payload.party_size},
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=table.store_id,
        event_type="session.opened",
        table_id=table.id,
        session_id=row.id,
        payload={"party_size": payload.party_size},
    )
    return TableSessionResponse.model_validate(row)


async def close_session(
    session: AsyncSession,
    session_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> TableSessionResponse:
    row = await _load_session(session, session_id, tenant_id)
    if row.status != TableSessionStatus.OPEN:
        raise ConflictError(
            message=f"session already {row.status.value}",
            details={"current": row.status.value},
        )
    row.status = TableSessionStatus.CLOSED
    row.closed_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="table_session.closed",
        tenant_id=tenant_id,
        store_id=row.store_id,
        actor_id=actor_id,
        target=("table_sessions", row.id),
        before={"status": "OPEN"},
        after={"status": "CLOSED"},
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=row.store_id,
        event_type="session.closed",
        table_id=row.table_id,
        session_id=row.id,
    )
    return TableSessionResponse.model_validate(row)


async def cancel_session(
    session: AsyncSession,
    session_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> TableSessionResponse:
    row = await _load_session(session, session_id, tenant_id)
    if row.status != TableSessionStatus.OPEN:
        raise ConflictError(
            message=f"session already {row.status.value}",
            details={"current": row.status.value},
        )
    row.status = TableSessionStatus.CANCELLED
    row.closed_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="table_session.cancelled",
        tenant_id=tenant_id,
        store_id=row.store_id,
        actor_id=actor_id,
        target=("table_sessions", row.id),
        before={"status": "OPEN"},
        after={"status": "CANCELLED"},
        reason=reason,
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=row.store_id,
        event_type="session.cancelled",
        table_id=row.table_id,
        session_id=row.id,
    )
    return TableSessionResponse.model_validate(row)


async def transfer_session(
    session: AsyncSession,
    session_id: uuid.UUID,
    payload: TableSessionTransfer,
    *,
    tenant_id: uuid.UUID,
) -> TableSessionResponse:
    row = await _load_session(session, session_id, tenant_id)
    if row.status != TableSessionStatus.OPEN:
        raise ConflictError(
            message=f"cannot transfer a {row.status.value} session",
            details={"current": row.status.value},
        )
    target = await _load_table(session, payload.target_table_id, tenant_id)
    if target.id == row.table_id:
        raise ConflictError(
            message="session is already at that table",
            details={"table_id": str(target.id)},
        )
    if target.store_id != row.store_id:
        raise ConflictError(
            message="cannot transfer a session across stores",
            details={"from_store": str(row.store_id), "to_store": str(target.store_id)},
        )
    if await _open_session_for_table(session, target.id) is not None:
        raise ConflictError(
            message="target table already has an open session",
            details={"target_table_id": str(target.id)},
        )
    from_table_id = row.table_id
    row.table_id = target.id
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="table_session.transferred",
        tenant_id=tenant_id,
        store_id=row.store_id,
        actor_id=payload.actor_id,
        target=("table_sessions", row.id),
        before={"table_id": str(from_table_id)},
        after={"table_id": str(target.id)},
        reason=payload.reason,
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=row.store_id,
        event_type="session.transferred",
        table_id=target.id,
        session_id=row.id,
        payload={"from_table_id": str(from_table_id), "to_table_id": str(target.id)},
    )
    return TableSessionResponse.model_validate(row)


# ──────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────


async def _load_table(
    session: AsyncSession, table_id: uuid.UUID, tenant_id: uuid.UUID
) -> DiningTable:
    row = (
        await session.execute(
            select(DiningTable).where(
                DiningTable.id == table_id,
                DiningTable.tenant_id == tenant_id,
                DiningTable.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"dining table {table_id} not found",
            details={"table_id": str(table_id)},
        )
    return row


async def _load_session(
    session: AsyncSession, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> TableSession:
    row = (
        await session.execute(
            select(TableSession).where(
                TableSession.id == session_id,
                TableSession.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"table session {session_id} not found",
            details={"session_id": str(session_id)},
        )
    return row


async def _open_session_for_table(
    session: AsyncSession, table_id: uuid.UUID
) -> TableSession | None:
    return (
        await session.execute(
            select(TableSession).where(
                TableSession.table_id == table_id,
                TableSession.status == TableSessionStatus.OPEN,
            )
        )
    ).scalar_one_or_none()


async def _guard_table_name_unique(
    session: AsyncSession, *, tenant_id: uuid.UUID, store_id: uuid.UUID, name: str
) -> None:
    exists = (
        await session.execute(
            select(DiningTable.id).where(
                DiningTable.tenant_id == tenant_id,
                DiningTable.store_id == store_id,
                DiningTable.name == name,
                DiningTable.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise ConflictError(
            message=f"table name {name!r} already exists in this store",
            details={"name": name},
        )


__all__ = [
    "cancel_session",
    "close_session",
    "create_table",
    "delete_table",
    "floor_plan",
    "list_tables",
    "open_session",
    "transfer_session",
    "update_table",
]
