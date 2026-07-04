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


@pytest_asyncio.fixture
async def _engine() -> AsyncIterator:
    """Function-scoped engine — pytest-asyncio 1.x rejects session-scoped
    async fixtures unless they explicitly declare ``loop_scope="session"``.
    Engine creation is cheap relative to the savepoint round-trip, so
    per-test scope keeps things simple and isolated.
    """
    if not _DB_AVAILABLE:
        pytest.skip("Postgres not reachable")
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    try:
        yield engine
    finally:
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


_ALL_SYSTEM_PERMISSIONS = frozenset({
    "orders:create", "orders:read", "orders:close", "orders:void", "orders:refund",
    "stock:intake", "stock:read", "stock:adjust",
    "clock:read_self", "clock:read_store", "clock:admin",
    "cost:create", "cost:read",
    "visual_assets:read", "visual_assets:write",
    "visual_assets:read_brand_ref", "visual_assets:manage_visibility", "visual_assets:read_all",
    "events:emit",
    "auth:manage_users", "auth:manage_roles", "auth:reset_password",
    "admin:tenant_settings", "admin:audit_read", "admin:export_data",
})


def _mock_owner_user(tenant_id: uuid.UUID, employee_id: uuid.UUID | None = None) -> Any:
    """Build a mock CurrentUser with owner-level permissions for tests
    that predate RBAC. Import is deferred so this module stays importable
    when auth code isn't on the path.
    """
    from restaurant_api.services.rbac_service import CurrentUser

    return CurrentUser(
        employee_id=employee_id or uuid.uuid4(),
        tenant_id=tenant_id,
        employee_role="owner",
        functional_roles=frozenset({"owner"}),
        permissions=_ALL_SYSTEM_PERMISSIONS,
        jti="test-jti-owner",
    )


def _mock_role_user(
    tenant_id: uuid.UUID,
    *,
    role_name: str,
    permissions: frozenset[str],
    employee_id: uuid.UUID | None = None,
) -> Any:
    """Build a mock CurrentUser scoped to a specific functional role."""
    from restaurant_api.services.rbac_service import CurrentUser

    return CurrentUser(
        employee_id=employee_id or uuid.uuid4(),
        tenant_id=tenant_id,
        employee_role="staff",  # default; overrideable via employee_role kwarg later
        functional_roles=frozenset({role_name}),
        permissions=permissions,
        jti=f"test-jti-{role_name}",
    )


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, seed_tenant: Tenant
) -> AsyncIterator[Any]:
    """``httpx.AsyncClient`` over ``ASGITransport`` so HTTP requests and the
    DB session share one event loop. The sync ``TestClient`` runs the app
    on anyio's blocking portal (different loop), which causes asyncpg
    futures to be attached to the wrong loop — symptom: "got Future
    attached to a different loop".

    Existing router tests predate RBAC (Phase 2). To keep them green
    without editing every file, this fixture overrides ``get_current_user``
    to return an owner-level mock user scoped to ``seed_tenant`` — the
    same tenant the seed fixtures use, so ``assert_tenant_match``
    passes automatically.
    """
    import httpx

    from restaurant_api.api.auth import AdminPrincipal, require_admin
    from restaurant_api.api.deps import get_current_user, get_db

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    owner_user = _mock_owner_user(seed_tenant.id)

    async def _override_current_user() -> Any:
        return owner_user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_admin] = lambda: AdminPrincipal()
    app.dependency_overrides[get_current_user] = _override_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def role_client_factory(
    db_session: AsyncSession, seed_tenant: Tenant
) -> AsyncIterator[Any]:
    """Factory fixture: hand it a role name + permission set, get back
    an authenticated ``httpx.AsyncClient`` that impersonates that role.

    Used by the new "no permission → 403" and "cross-tenant → 404"
    rejection tests. Yields an async factory; the client is torn down
    when the outer fixture exits.
    """
    import httpx

    from restaurant_api.api.deps import get_current_user, get_db

    clients: list[Any] = []

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _factory(
        role_name: str,
        permissions: frozenset[str] | set[str],
        *,
        tenant_id: uuid.UUID | None = None,
    ) -> Any:
        user = _mock_role_user(
            tenant_id or seed_tenant.id,
            role_name=role_name,
            permissions=frozenset(permissions),
        )

        async def _override_user() -> Any:
            return user

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        transport = httpx.ASGITransport(app=app)
        c = httpx.AsyncClient(transport=transport, base_url="http://test")
        clients.append(c)
        return c

    yield _factory

    for c in clients:
        await c.aclose()
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def anon_client(db_session: AsyncSession) -> AsyncIterator[Any]:
    """Like ``client`` but WITHOUT the admin-auth override — for testing the
    login flow and the 401 gate on protected endpoints.
    """
    import httpx

    from restaurant_api.api.deps import get_db

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
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
