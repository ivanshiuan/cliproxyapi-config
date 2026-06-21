"""Integration tests for ``/events`` (waste / staff_meal / tasting).

Hits the FastAPI app via ``httpx.AsyncClient + ASGITransport`` so the
async session in the test fixture and the async session inside the route
handler share the **same** event loop. The sync ``TestClient`` from
conftest can't be used here: it spawns a background thread that calls into
async route code via ``anyio.from_thread``, which gives the route a
*different* event loop from the test's ``AsyncSession`` connection — and
asyncpg connections are loop-bound.

DB dependency: auto-skipped via ``conftest._DB_AVAILABLE`` when Postgres
isn't reachable.

Acceptance-criteria mapping
---------------------------
- AC-1  -> test_post_waste_creates_row_and_movement
- AC-3  -> test_post_staff_meal_via_menu_item_creates_movements
- AC-7  -> test_post_tasting_via_menu_item_creates_movements
- AC-10 -> test_get_events_filters_by_type
- AC-11 -> test_get_events_filters_by_date_range
- AC-13 -> test_idempotency_same_external_ref_returns_existing
- AC-14 -> test_float_qty_rejected
- AC-12 -> test_no_mutation_endpoints_exposed
- AC-5/6 -> test_staff_meal_neither_source_rejected (bonus)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from restaurant_api.api.deps import get_db
from restaurant_api.config import get_settings
from restaurant_api.main import app
from restaurant_api.models import (
    Ingredient,
    MovementType,
    Recipe,
    StaffMealEvent,
    StockMovement,
    Store,
    WasteEvent,
)


# Override the session-scoped ``_engine`` fixture from ``tests/conftest.py``
# with a function-scoped one. pytest-asyncio 1.x rejects session-scoped
# async fixtures when ``asyncio_default_fixture_loop_scope = function`` is
# set (it is in pyproject.toml); a per-function engine sidesteps the scope
# clash without changing the shared conftest.
@pytest_asyncio.fixture
async def _engine() -> AsyncIterator[Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client that shares the test's session — no cross-loop traps."""

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


pytestmark = pytest.mark.asyncio


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


async def _second_ingredient(
    db_session: AsyncSession, seed_tenant: Any, seed_store: Store
) -> Ingredient:
    ing = Ingredient(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        name="Bun",
        unit="piece",
        standard_cost_per_unit=Decimal("8.0000"),
        is_active=True,
    )
    db_session.add(ing)
    await db_session.flush()
    return ing


# ──────────────────────────────────────────────────────────────────────────
# AC-1  POST /events/waste
# ──────────────────────────────────────────────────────────────────────────


async def test_post_waste_creates_row_and_movement(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store: Store,
    seed_ingredient: Ingredient,
    seed_employee: Any,
) -> None:
    resp = await async_client.post(
        "/events/waste",
        json={
            "store_id": str(seed_store.id),
            "ingredient_id": str(seed_ingredient.id),
            "qty": "0.5",
            "cost": "87.50",
            "reason": "expired",
            "reported_by": str(seed_employee.id),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event_type"] == "waste"
    assert Decimal(body["qty"]) == Decimal("0.5")
    assert Decimal(body["cost"]) == Decimal("87.50")
    assert body["movement_id"] is not None

    we_rows = (
        (
            await db_session.execute(
                select(WasteEvent).where(WasteEvent.id == uuid.UUID(body["id"]))
            )
        )
        .scalars()
        .all()
    )
    assert len(we_rows) == 1

    sm_rows = (
        (
            await db_session.execute(
                select(StockMovement).where(
                    StockMovement.source_table == "waste_events",
                    StockMovement.source_id == uuid.UUID(body["id"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(sm_rows) == 1
    assert sm_rows[0].movement_type == MovementType.WASTE
    assert sm_rows[0].qty == Decimal("-0.5000")


# ──────────────────────────────────────────────────────────────────────────
# AC-3  POST /events/staff-meal (menu_item path → BOM expansion)
# ──────────────────────────────────────────────────────────────────────────


async def test_post_staff_meal_via_menu_item_creates_movements(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store: Store,
    seed_employee: Any,
    seed_menu_item: Any,
    seed_ingredient: Ingredient,
    seed_recipe: Recipe,
    seed_tenant: Any,
) -> None:
    # 2nd recipe line so we exercise N>1 movements per event.
    ing2 = await _second_ingredient(db_session, seed_tenant, seed_store)
    db_session.add(
        Recipe(
            tenant_id=seed_tenant.id,
            menu_item_id=seed_menu_item.id,
            ingredient_id=ing2.id,
            qty_per_serving=Decimal("2.0000"),
        )
    )
    await db_session.flush()

    resp = await async_client.post(
        "/events/staff-meal",
        json={
            "store_id": str(seed_store.id),
            "employee_id": str(seed_employee.id),
            "menu_item_id": str(seed_menu_item.id),
            "total_cost": "120.00",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event_type"] == "staff_meal"
    assert body["movements_created"] == 2

    sm_event = (
        await db_session.execute(
            select(StaffMealEvent).where(StaffMealEvent.id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert sm_event.menu_item_id == seed_menu_item.id

    movements = (
        (
            await db_session.execute(
                select(StockMovement).where(
                    StockMovement.source_table == "staff_meal_events",
                    StockMovement.source_id == uuid.UUID(body["id"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(movements) == 2
    for m in movements:
        assert m.movement_type == MovementType.STAFF_MEAL
        assert m.qty < 0


# ──────────────────────────────────────────────────────────────────────────
# AC-7  POST /events/tasting (menu_item path → BOM expansion)
# ──────────────────────────────────────────────────────────────────────────


async def test_post_tasting_via_menu_item_creates_movements(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store: Store,
    seed_menu_item: Any,
    seed_ingredient: Ingredient,
    seed_recipe: Recipe,
) -> None:
    resp = await async_client.post(
        "/events/tasting",
        json={
            "store_id": str(seed_store.id),
            "menu_item_id": str(seed_menu_item.id),
            "qty": "1.0",
            "total_cost": "40.00",
            "purpose": "rnd",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event_type"] == "tasting"
    assert body["movements_created"] == 1

    movements = (
        (
            await db_session.execute(
                select(StockMovement).where(
                    StockMovement.source_table == "tasting_events",
                    StockMovement.source_id == uuid.UUID(body["id"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(movements) == 1
    assert movements[0].movement_type == MovementType.TASTING
    assert movements[0].qty < 0


# ──────────────────────────────────────────────────────────────────────────
# AC-10  GET /events filters by type
# ──────────────────────────────────────────────────────────────────────────


async def test_get_events_filters_by_type(
    async_client: httpx.AsyncClient,
    seed_store: Store,
    seed_ingredient: Ingredient,
    seed_employee: Any,
    seed_menu_item: Any,
    seed_recipe: Recipe,
) -> None:
    # Seed one of each.
    r1 = await async_client.post(
        "/events/waste",
        json={
            "store_id": str(seed_store.id),
            "ingredient_id": str(seed_ingredient.id),
            "qty": "1.0",
            "cost": "10.00",
            "reason": "dropped",
        },
    )
    assert r1.status_code == 201, r1.text
    r2 = await async_client.post(
        "/events/staff-meal",
        json={
            "store_id": str(seed_store.id),
            "employee_id": str(seed_employee.id),
            "menu_item_id": str(seed_menu_item.id),
            "total_cost": "50.00",
        },
    )
    assert r2.status_code == 201, r2.text
    r3 = await async_client.post(
        "/events/tasting",
        json={
            "store_id": str(seed_store.id),
            "ingredient_id": str(seed_ingredient.id),
            "qty": "0.5",
            "total_cost": "20.00",
            "purpose": "rnd",
        },
    )
    assert r3.status_code == 201, r3.text

    resp = await async_client.get(
        "/events",
        params={"event_type": "waste", "store_id": str(seed_store.id)},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    assert items[0]["event_type"] == "waste"


# ──────────────────────────────────────────────────────────────────────────
# AC-11  GET /events filters by date range (TPE day, inclusive)
# ──────────────────────────────────────────────────────────────────────────


async def test_get_events_filters_by_date_range(
    async_client: httpx.AsyncClient,
    seed_store: Store,
    seed_ingredient: Ingredient,
) -> None:
    # 2025-05-01 TPE 23:59  ==  2025-05-01T15:59:00+00:00
    early = datetime(2025, 5, 1, 15, 59, 0, tzinfo=UTC)
    # 2025-05-02 TPE 00:01  ==  2025-05-01T16:01:00+00:00
    late = datetime(2025, 5, 1, 16, 1, 0, tzinfo=UTC)

    for occurred in (early, late):
        resp = await async_client.post(
            "/events/waste",
            json={
                "store_id": str(seed_store.id),
                "ingredient_id": str(seed_ingredient.id),
                "qty": "1.0",
                "cost": "5.00",
                "reason": "spoiled",
                "occurred_at": occurred.isoformat(),
            },
        )
        assert resp.status_code == 201, resp.text

    resp = await async_client.get(
        "/events",
        params={
            "event_type": "waste",
            "from_date": "2025-05-02",
            "to_date": "2025-05-02",
            "store_id": str(seed_store.id),
        },
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1, items


# ──────────────────────────────────────────────────────────────────────────
# AC-13  Idempotency
# ──────────────────────────────────────────────────────────────────────────


async def test_idempotency_same_external_ref_returns_existing(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store: Store,
    seed_ingredient: Ingredient,
) -> None:
    payload = {
        "store_id": str(seed_store.id),
        "ingredient_id": str(seed_ingredient.id),
        "qty": "1.0",
        "cost": "5.00",
        "reason": "dropped",
        "external_ref": "client-token-42",
    }
    first = await async_client.post("/events/waste", json=payload)
    assert first.status_code == 201, first.text
    second = await async_client.post("/events/waste", json=payload)
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]

    we_count = len(
        (
            await db_session.execute(
                select(WasteEvent).where(WasteEvent.store_id == seed_store.id)
            )
        )
        .scalars()
        .all()
    )
    assert we_count == 1
    sm_count = len(
        (
            await db_session.execute(
                select(StockMovement).where(
                    StockMovement.source_table == "waste_events",
                    StockMovement.store_id == seed_store.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert sm_count == 1


# ──────────────────────────────────────────────────────────────────────────
# AC-14  Float qty rejected
# ──────────────────────────────────────────────────────────────────────────


async def test_float_qty_rejected(
    async_client: httpx.AsyncClient,
    seed_store: Store,
    seed_ingredient: Ingredient,
) -> None:
    resp = await async_client.post(
        "/events/waste",
        json={
            "store_id": str(seed_store.id),
            "ingredient_id": str(seed_ingredient.id),
            "qty": 0.5,  # float literal → BeforeValidator raises
            "cost": "10.00",
            "reason": "dropped",
        },
    )
    assert resp.status_code == 422, resp.text


# ──────────────────────────────────────────────────────────────────────────
# AC-12  Immutability — no mutation endpoints in OpenAPI
# ──────────────────────────────────────────────────────────────────────────


async def test_no_mutation_endpoints_exposed(async_client: httpx.AsyncClient) -> None:
    resp = await async_client.get("/openapi.json")
    schema = resp.json()
    for path, methods in schema["paths"].items():
        if not path.startswith("/events"):
            continue
        forbidden = set(methods.keys()) & {"patch", "put", "delete"}
        assert not forbidden, f"{path} exposes {forbidden}"


# ──────────────────────────────────────────────────────────────────────────
# Bonus (AC-5 / AC-6): cross-field validation for staff_meal
# ──────────────────────────────────────────────────────────────────────────


async def test_staff_meal_neither_source_rejected(
    async_client: httpx.AsyncClient,
    seed_store: Store,
    seed_employee: Any,
) -> None:
    resp = await async_client.post(
        "/events/staff-meal",
        json={
            "store_id": str(seed_store.id),
            "employee_id": str(seed_employee.id),
            "total_cost": "50.00",
        },
    )
    assert resp.status_code == 422, resp.text
