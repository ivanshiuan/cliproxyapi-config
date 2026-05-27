"""Integration tests for the ``/stock`` router.

These tests run against a real Postgres (auto-skipped when none is reachable
via the ``needs_db`` marker added by ``tests/conftest.py``).

Two infrastructure overrides live at the top of this file:

1.  ``_engine``: the shared conftest declares this fixture as
    ``scope="session"`` but on pytest-asyncio 1.x it lacks a matching
    ``loop_scope="session"``, so it can't resolve against the
    function-scoped default runner. We re-declare it as function-scoped
    here — slightly slower (one engine per test) but isolated and correct.
2.  ``client``: the shared fixture uses FastAPI's sync ``TestClient`` whose
    ``anyio.BlockingPortal`` lives on a different loop than the async
    SQLAlchemy session, which causes asyncpg's "Future attached to a
    different loop" error. We use ``httpx.AsyncClient`` + ``ASGITransport``
    so HTTP calls and the DB session share a single asyncio loop.

The ``stock`` router is also registered onto ``main.app`` here — once
``main.py`` mounts it for real, the guarded ``include_router`` becomes a
no-op.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.config import get_settings
from restaurant_api.main import app
from restaurant_api.models import Ingredient, StockMovement, Tenant
from restaurant_api.routers import stock as stock_router_module

# Ensure the router is mounted exactly once for the whole test session.
if not any(getattr(r, "path", "").startswith("/stock") for r in app.routes):
    app.include_router(stock_router_module.router)


# ──────────────────────────────────────────────────────────────────────────
# Fixture overrides (see module docstring for rationale)
# ──────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def _engine() -> AsyncIterator:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, seed_tenant: Tenant
) -> AsyncIterator[AsyncClient]:
    """Async HTTP client that shares this test's asyncio loop with db_session.

    Also overrides ``get_current_tenant_id`` to return the seeded tenant so
    that tenant-scoped lookups (store, ingredient) inside the service match
    the fixtures' tenant — the default-tenant fallback would otherwise yield
    404s for every request.
    """

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> Any:
        return seed_tenant.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _purchase_payload(
    *,
    store_id: str,
    ingredient_id: str,
    qty: str = "10",
    unit_cost: str = "3.5000",
    lot_no: str | None = "LOT-A",
    invoice_number: str = "INV-001",
    supplier_name: str = "Acme Foods",
    supplier_tax_id: str | None = "12345678",
    external_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "store_id": store_id,
        "supplier_name": supplier_name,
        "supplier_tax_id": supplier_tax_id,
        "invoice_number": invoice_number,
        "invoice_date": date.today().isoformat(),
        "external_ref": external_ref,
        "lines": [
            {
                "ingredient_id": ingredient_id,
                "qty": qty,
                "unit_cost": unit_cost,
                "lot_no": lot_no,
            }
        ],
    }


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purchase_records_movement_with_positive_qty_and_lot_no(
    client, seed_store, seed_ingredient
):
    """AC-1: a 1-line purchase writes one positive-qty stock_movement and
    propagates ``lot_no`` from the request onto the ledger row."""
    payload = _purchase_payload(
        store_id=str(seed_store.id),
        ingredient_id=str(seed_ingredient.id),
        lot_no="LOT-XYZ-42",
    )
    resp = await client.post("/stock/purchases", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(body["lines"][0]["qty"]) == Decimal("10")
    assert body["lines"][0]["lot_no"] == "LOT-XYZ-42"
    assert Decimal(body["total"]) == Decimal("35.0000")

    # Round-trip from GET /stock/movements
    movements_resp = await client.get(
        "/stock/movements", params={"ingredient_id": str(seed_ingredient.id)}
    )
    assert movements_resp.status_code == 200
    rows = movements_resp.json()
    assert len(rows) == 1
    assert rows[0]["movement_type"] == "purchase"
    assert Decimal(rows[0]["qty"]) > 0
    assert rows[0]["lot_no"] == "LOT-XYZ-42"


@pytest.mark.asyncio
async def test_adjustment_positive_delta_becomes_adjustment_in(
    client, seed_store, seed_ingredient
):
    """AC-5: a positive ``delta`` creates an ``adjustment_in`` row."""
    payload = {
        "store_id": str(seed_store.id),
        "ingredient_id": str(seed_ingredient.id),
        "delta": "5.0000",
        "reason": "盤點補帳",
    }
    resp = await client.post("/stock/adjustments", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(body["delta"]) == Decimal("5.0000")

    movements_resp = await client.get(
        "/stock/movements",
        params={
            "ingredient_id": str(seed_ingredient.id),
            "movement_type": "adjustment_in",
        },
    )
    assert movements_resp.status_code == 200
    rows = movements_resp.json()
    assert len(rows) == 1
    assert rows[0]["movement_type"] == "adjustment_in"
    assert Decimal(rows[0]["qty"]) == Decimal("5.0000")


@pytest.mark.asyncio
async def test_adjustment_negative_delta_becomes_adjustment_out(
    client, seed_store, seed_ingredient
):
    """AC-6: a negative ``delta`` creates an ``adjustment_out`` row that
    preserves the sign (DB CHECK requires qty < 0 for outbound types)."""
    payload = {
        "store_id": str(seed_store.id),
        "ingredient_id": str(seed_ingredient.id),
        "delta": "-3.0000",
        "reason": "estimation correction",
    }
    resp = await client.post("/stock/adjustments", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(body["delta"]) == Decimal("-3.0000")

    movements_resp = await client.get(
        "/stock/movements",
        params={
            "ingredient_id": str(seed_ingredient.id),
            "movement_type": "adjustment_out",
        },
    )
    rows = movements_resp.json()
    assert len(rows) == 1
    assert rows[0]["movement_type"] == "adjustment_out"
    assert Decimal(rows[0]["qty"]) == Decimal("-3.0000")


@pytest.mark.asyncio
async def test_movements_filter_by_ingredient_id(
    client, db_session, seed_store, seed_tenant, seed_ingredient
):
    """AC-9: ``GET /stock/movements?ingredient_id=X`` only returns rows for
    ingredient X — seed two ingredients with two movements each."""
    other = Ingredient(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        name="Onion",
        unit="kg",
        standard_cost_per_unit=Decimal("20.0000"),
        is_active=True,
    )
    db_session.add(other)
    await db_session.flush()

    # 2 purchases per ingredient.
    for ing_id, inv in [
        (seed_ingredient.id, "INV-S1"),
        (seed_ingredient.id, "INV-S2"),
        (other.id, "INV-O1"),
        (other.id, "INV-O2"),
    ]:
        payload = _purchase_payload(
            store_id=str(seed_store.id),
            ingredient_id=str(ing_id),
            invoice_number=inv,
            supplier_tax_id=None,  # avoid the invoice-conflict path
        )
        resp = await client.post("/stock/purchases", json=payload)
        assert resp.status_code == 201, resp.text

    rows = (
        await client.get(
            "/stock/movements", params={"ingredient_id": str(seed_ingredient.id)}
        )
    ).json()
    assert len(rows) == 2
    assert all(r["ingredient_id"] == str(seed_ingredient.id) for r in rows)


@pytest.mark.asyncio
async def test_movements_filter_by_date_range(client, seed_store, seed_ingredient):
    """AC-11: ``from_ts`` / ``to_ts`` correctly bracket occurred_at, and
    inverted bounds are rejected with 422."""
    payload = {
        "store_id": str(seed_store.id),
        "ingredient_id": str(seed_ingredient.id),
        "delta": "1",
        "reason": "now",
    }
    resp = await client.post("/stock/adjustments", json=payload)
    assert resp.status_code == 201, resp.text

    # Past window excludes the just-inserted row.
    past_start = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    past_end = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    rows = (
        await client.get(
            "/stock/movements",
            params={
                "ingredient_id": str(seed_ingredient.id),
                "from_ts": past_start,
                "to_ts": past_end,
            },
        )
    ).json()
    assert rows == []

    # Wide window catches it.
    wide_start = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    wide_end = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    rows = (
        await client.get(
            "/stock/movements",
            params={
                "ingredient_id": str(seed_ingredient.id),
                "from_ts": wide_start,
                "to_ts": wide_end,
            },
        )
    ).json()
    assert len(rows) == 1

    # Inverted bounds → 422.
    bad = await client.get(
        "/stock/movements", params={"from_ts": wide_end, "to_ts": wide_start}
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_pagination_limit_clamped_and_offset_respected(
    client, seed_store, seed_ingredient
):
    """Pagination: ``limit`` is bounded at 200, ``offset`` works.

    Create three movements; probe both bounds. Asking for ``limit=999``
    is rejected at the route layer (FastAPI ``Query(le=200)`` → 422), which
    is the spec's "clamp" expressed as a hard error. ``offset=2`` skips
    the first two rows and returns the remaining one.
    """
    for i in range(3):
        resp = await client.post(
            "/stock/adjustments",
            json={
                "store_id": str(seed_store.id),
                "ingredient_id": str(seed_ingredient.id),
                "delta": f"{i + 1}",
                "reason": f"row-{i}",
            },
        )
        assert resp.status_code == 201, resp.text

    over_limit = await client.get(
        "/stock/movements",
        params={"ingredient_id": str(seed_ingredient.id), "limit": 999},
    )
    assert over_limit.status_code == 422

    page1 = (
        await client.get(
            "/stock/movements",
            params={"ingredient_id": str(seed_ingredient.id), "limit": 2, "offset": 0},
        )
    ).json()
    assert len(page1) == 2

    page2 = (
        await client.get(
            "/stock/movements",
            params={"ingredient_id": str(seed_ingredient.id), "limit": 2, "offset": 2},
        )
    ).json()
    assert len(page2) == 1
    page1_ids = {r["id"] for r in page1}
    assert page2[0]["id"] not in page1_ids


@pytest.mark.asyncio
async def test_float_qty_in_purchase_is_rejected(
    client, seed_store, seed_ingredient
):
    """AC-13: a JSON ``float`` for ``qty`` is rejected with 422 — our
    ``BeforeValidator`` refuses floats so monetary precision is never lost
    to IEEE-754 round-trips through ``json.loads``."""
    payload = {
        "store_id": str(seed_store.id),
        "supplier_name": "Acme",
        "invoice_number": "INV-FLT",
        "invoice_date": date.today().isoformat(),
        "lines": [
            {
                "ingredient_id": str(seed_ingredient.id),
                "qty": 1.5,  # float, not a string — must be rejected.
                "unit_cost": "1.0000",
            }
        ],
    }
    resp = await client.post("/stock/purchases", json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_append_only_round_trip_no_update_or_delete(
    client, db_session, seed_store, seed_ingredient
):
    """AC-8: the ledger is append-only at the *service* layer too — once
    inserted, a movement's stored bytes never mutate. We snapshot ``count``
    and the row contents before/after a follow-up adjustment and assert the
    earlier row is byte-identical (id/qty/note unchanged), while the count
    only increases."""
    initial_count = (
        await db_session.execute(select(func.count(StockMovement.id)))
    ).scalar_one()

    resp = await client.post(
        "/stock/adjustments",
        json={
            "store_id": str(seed_store.id),
            "ingredient_id": str(seed_ingredient.id),
            "delta": "7",
            "reason": "first",
        },
    )
    assert resp.status_code == 201, resp.text
    first_id = resp.json()["movement_id"]

    after_first = (
        await db_session.execute(select(func.count(StockMovement.id)))
    ).scalar_one()
    assert after_first == initial_count + 1

    first_row = (
        await db_session.execute(
            select(StockMovement).where(StockMovement.id == first_id)
        )
    ).scalar_one()
    snapshot = (
        first_row.qty,
        first_row.note,
        first_row.movement_type,
        first_row.occurred_at,
    )

    # Correcting adjustment — must INSERT another row, never UPDATE/DELETE.
    resp2 = await client.post(
        "/stock/adjustments",
        json={
            "store_id": str(seed_store.id),
            "ingredient_id": str(seed_ingredient.id),
            "delta": "-7",
            "reason": f"reversal of {first_id}",
        },
    )
    assert resp2.status_code == 201, resp2.text

    after_second = (
        await db_session.execute(select(func.count(StockMovement.id)))
    ).scalar_one()
    assert after_second == initial_count + 2  # +1 row, never -1

    # Reload first row — its content must be unchanged. Expire the identity
    # map so the SELECT actually hits the DB.
    db_session.expire_all()
    reloaded = (
        await db_session.execute(
            select(StockMovement).where(StockMovement.id == first_id)
        )
    ).scalar_one()
    assert (
        reloaded.qty,
        reloaded.note,
        reloaded.movement_type,
        reloaded.occurred_at,
    ) == snapshot
