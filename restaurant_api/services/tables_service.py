"""Dining-table business logic — 桌位 CRUD for the ``/tables`` router.

Boundary rules (same contract as the other services in this repo):
- ``flush()`` only, never ``commit()`` — the transaction boundary lives in
  ``api/deps.get_db``.
- Domain errors raise ``DomainError`` subclasses; the router never
  translates exceptions.
- Name uniqueness among live rows is pre-checked here for a friendly 409,
  and backstopped by the DB partial unique index
  ``uq_dining_tables_store_name_live`` (soft-deleted rows free the name).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..models import DiningTable
from ..schemas.tables import TableCreateRequest, TablePatchRequest, TableResponse
from .audit_service import audit

logger = logging.getLogger("restaurant_api.services.tables")


async def _live_name_taken(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    """True when a live (non-soft-deleted) table already holds this name."""
    stmt = select(DiningTable.id).where(
        DiningTable.tenant_id == tenant_id,
        DiningTable.store_id == store_id,
        DiningTable.name == name,
        DiningTable.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(DiningTable.id != exclude_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _get_live_table(
    session: AsyncSession,
    table_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
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
            f"Dining table {table_id} not found",
            details={"table_id": str(table_id)},
        )
    return row


async def create_table(
    session: AsyncSession,
    payload: TableCreateRequest,
    *,
    tenant_id: uuid.UUID,
) -> TableResponse:
    if await _live_name_taken(
        session,
        tenant_id=tenant_id,
        store_id=payload.store_id,
        name=payload.name,
    ):
        raise ConflictError(
            f"Table name {payload.name!r} already exists in this store",
            details={"store_id": str(payload.store_id), "name": payload.name},
        )

    row = DiningTable(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        name=payload.name,
        zone=payload.zone,
        capacity=payload.capacity,
        sort_order=payload.sort_order,
    )
    session.add(row)
    await session.flush()

    await audit(
        session,
        action="dining_table.created",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        target=("dining_tables", row.id),
        after={
            "name": row.name,
            "zone": row.zone,
            "capacity": row.capacity,
            "sort_order": row.sort_order,
        },
    )
    logger.info("dining_table.created table_id=%s name=%s", row.id, row.name)
    return TableResponse.model_validate(row)


async def list_tables(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    include_inactive: bool = False,
    limit: int = 500,
) -> list[TableResponse]:
    """List live tables. Soft-deleted rows are never returned; inactive
    (front-end hidden) tables only show when ``include_inactive`` is set."""
    stmt = select(DiningTable).where(
        DiningTable.tenant_id == tenant_id,
        DiningTable.deleted_at.is_(None),
    )
    if store_id is not None:
        stmt = stmt.where(DiningTable.store_id == store_id)
    if not include_inactive:
        stmt = stmt.where(DiningTable.is_active.is_(True))
    stmt = stmt.order_by(DiningTable.sort_order, DiningTable.name).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [TableResponse.model_validate(r) for r in rows]


async def patch_table(
    session: AsyncSession,
    table_id: uuid.UUID,
    payload: TablePatchRequest,
    *,
    tenant_id: uuid.UUID,
) -> TableResponse:
    row = await _get_live_table(session, table_id, tenant_id=tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return TableResponse.model_validate(row)

    new_name = changes.get("name")
    if (
        new_name is not None
        and new_name != row.name
        and await _live_name_taken(
            session,
            tenant_id=tenant_id,
            store_id=row.store_id,
            name=new_name,
            exclude_id=row.id,
        )
    ):
        raise ConflictError(
            f"Table name {new_name!r} already exists in this store",
            details={"store_id": str(row.store_id), "name": new_name},
        )

    before = {field: getattr(row, field) for field in changes}
    for field, value in changes.items():
        setattr(row, field, value)
    await session.flush()
    # onupdate refreshes updated_at server-side — reload so the response
    # serialiser doesn't trip on an expired attribute (MissingGreenlet).
    await session.refresh(row)

    await audit(
        session,
        action="dining_table.updated",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("dining_tables", row.id),
        before=before,
        after=changes,
    )
    logger.info(
        "dining_table.updated table_id=%s fields=%s", row.id, sorted(changes)
    )
    return TableResponse.model_validate(row)


async def delete_table(
    session: AsyncSession,
    table_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    """Soft delete — historical orders keep a resolvable table reference,
    and the (store, name) slot frees up for a re-layout."""
    row = await _get_live_table(session, table_id, tenant_id=tenant_id)
    row.deleted_at = datetime.now(UTC)
    row.is_active = False
    await session.flush()

    await audit(
        session,
        action="dining_table.deleted",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("dining_tables", row.id),
        before={"name": row.name, "is_active": True},
    )
    logger.info("dining_table.deleted table_id=%s name=%s", row.id, row.name)


__all__ = [
    "create_table",
    "delete_table",
    "list_tables",
    "patch_table",
]
