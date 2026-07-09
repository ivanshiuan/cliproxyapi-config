"""DB-level behaviour of the POS floor-plan tables (P1.1).

These pin the two invariants the schema is responsible for:

1. at most ONE open session per table (partial unique index), and
2. a soft-deleted table frees its name for re-creation, while two live
   tables in one store can never share a name.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import (
    DiningTable,
    Store,
    TableSession,
    TableSessionStatus,
    Tenant,
)


async def _table(
    db_session: AsyncSession, tenant: Tenant, store: Store, name: str = "A1"
) -> DiningTable:
    t = DiningTable(tenant_id=tenant.id, store_id=store.id, name=name, seats=4)
    db_session.add(t)
    await db_session.flush()
    return t


@pytest.mark.asyncio
async def test_second_open_session_on_same_table_is_rejected(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    table = await _table(db_session, seed_tenant, seed_store)
    db_session.add(
        TableSession(
            tenant_id=seed_tenant.id, store_id=seed_store.id, table_id=table.id, party_size=2
        )
    )
    await db_session.flush()

    db_session.add(
        TableSession(
            tenant_id=seed_tenant.id, store_id=seed_store.id, table_id=table.id, party_size=3
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_closed_session_frees_the_table_for_a_new_open(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    table = await _table(db_session, seed_tenant, seed_store)
    first = TableSession(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        table_id=table.id,
        party_size=2,
        status=TableSessionStatus.CLOSED,
        closed_at=datetime.now(UTC),
    )
    db_session.add(first)
    await db_session.flush()

    db_session.add(
        TableSession(
            tenant_id=seed_tenant.id, store_id=seed_store.id, table_id=table.id, party_size=4
        )
    )
    await db_session.flush()  # must not raise — only OPEN sessions are unique


@pytest.mark.asyncio
async def test_duplicate_live_table_name_rejected(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    await _table(db_session, seed_tenant, seed_store, name="B2")

    db_session.add(
        DiningTable(tenant_id=seed_tenant.id, store_id=seed_store.id, name="B2", seats=2)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_soft_deleted_table_name_is_reusable(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    original = await _table(db_session, seed_tenant, seed_store, name="B2")
    original.deleted_at = datetime.now(UTC)
    await db_session.flush()

    db_session.add(
        DiningTable(tenant_id=seed_tenant.id, store_id=seed_store.id, name="B2", seats=2)
    )
    await db_session.flush()  # soft-deleted row no longer blocks the name
