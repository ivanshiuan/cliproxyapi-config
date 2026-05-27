"""Shared pytest fixtures for router-level integration tests.

Tests use a real Postgres (the dev DB by default, ``resto_dev``). Each test
runs inside a SAVEPOINT-rolled transaction so no row leaks between tests.

Skip strategy:
- If Postgres isn't reachable, router tests collected from ``tests/routers``
  and ``tests/services`` are marked ``skipif`` automatically by ``_db_available``.
- Pure unit tests (``test_state``, ``test_workspace``, etc.) don't import
  these fixtures and run unaffected.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio  # type: ignore[import-not-found]  # only at runtime when fixtures used
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from restaurant_api.config import get_settings
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.integrations.line.messenger import reset_messenger
from restaurant_api.main import app
from restaurant_api.models import (
    Base,
    Employee,
    EmployeeRole,
    Ingredient,
    MenuCategory,
    MenuItem,
    Recipe,
    Store,
    Tenant,
)


# ──────────────────────────────────────────────────────────────────────────
# Detect DB availability — skip router tests cleanly when no Postgres around
# ──────────────────────────────────────────────────────────────────────────


def _probe_db_sync() -> bool:
    """Quick TCP probe so a missing DB doesn't make hundreds of tests fail."""
    import socket

    settings = get_settings()
    try:
        with socket.create_connection((settings.db_host, settings.db_port), timeout=1.0):
            return True
    except OSError:
        return False


_DB_AVAILABLE = _probe_db_sync()

needs_db = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason="Postgres not reachable at RESTO_DB_HOST:RESTO_DB_PORT; router tests skipped",
)


# ──────────────────────────────────────────────────────────────────────────
# Async engine + per-test rolled-back session
# ──────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="session")
async def _engine() -> AsyncIterator:
    if not _DB_AVAILABLE:
        pytest.skip("Postgres not reachable")
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine) -> AsyncIterator[AsyncSession]:
    """One savepoint per test. Rolls back on teardown — DB stays clean."""
    connection = await _engine.connect()
    trans = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await trans.rollback()
            await connection.close()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[Any]:
    """FastAPI TestClient with get_db overridden to the rolled-back session."""
    from fastapi.testclient import TestClient

    from restaurant_api.api.deps import get_db

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def stub_messenger() -> Iterator[StubLineMessenger]:
    """Reset the LINE messenger singleton to a fresh stub for the test."""
    reset_messenger()
    m = StubLineMessenger()
    # Force get_messenger() to return this instance for the duration of the test.
    import restaurant_api.integrations.line.messenger as line_mod

    line_mod._singleton = m
    yield m
    reset_messenger()


# ──────────────────────────────────────────────────────────────────────────
# Seed factories — keep tests short and focused on the behaviour under test
# ──────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_tenant(db_session: AsyncSession) -> Tenant:
    t = Tenant(name="Test Tenant", slug=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    await db_session.flush()
    return t


@pytest_asyncio.fixture
async def seed_store(db_session: AsyncSession, seed_tenant: Tenant) -> Store:
    s = Store(
        tenant_id=seed_tenant.id,
        name="Test Store",
        address="Test Address",
        phone="02-0000-0000",
        opened_on=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def seed_employee(db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store) -> Employee:
    e = Employee(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        full_name="Test Staff",
        role=EmployeeRole.STAFF,
        hourly_wage=Decimal("200.00"),
        hired_on=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(e)
    await db_session.flush()
    return e


@pytest_asyncio.fixture
async def seed_menu_item(db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store) -> MenuItem:
    cat = MenuCategory(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        name="Main Course",
        sort_order=1,
        is_active=True,
    )
    db_session.add(cat)
    await db_session.flush()

    item = MenuItem(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        category_id=cat.id,
        sku=f"SKU-{uuid.uuid4().hex[:8]}",
        name="Test Burger",
        price=Decimal("250.00"),
        cost_estimate=Decimal("80.00"),
        is_available=True,
        allergens=[],
    )
    db_session.add(item)
    await db_session.flush()
    return item


@pytest_asyncio.fixture
async def seed_ingredient(db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store) -> Ingredient:
    ing = Ingredient(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        name="Beef Patty",
        unit="piece",
        standard_cost_per_unit=Decimal("40.0000"),
        is_active=True,
    )
    db_session.add(ing)
    await db_session.flush()
    return ing


@pytest_asyncio.fixture
async def seed_recipe(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_menu_item: MenuItem,
    seed_ingredient: Ingredient,
) -> Recipe:
    r = Recipe(
        tenant_id=seed_tenant.id,
        menu_item_id=seed_menu_item.id,
        ingredient_id=seed_ingredient.id,
        qty_per_serving=Decimal("1.0000"),
    )
    db_session.add(r)
    await db_session.flush()
    return r


# Ensure asyncio fixtures co-operate with pytest_asyncio
def pytest_collection_modifyitems(config, items) -> None:  # type: ignore[no-untyped-def]
    for item in items:
        # Auto-skip the router/services dirs when DB unavailable.
        path = str(item.fspath)
        if ("tests/routers/" in path or "tests/services/" in path) and not _DB_AVAILABLE:
            item.add_marker(needs_db)
