"""Router-level tests for ``/orders``.

These are integration tests — they go through the real FastAPI app, a real
async SQLAlchemy session bound to a savepoint that gets rolled back at the
end of each test, and exercise the full schema → service → ORM path.

Coverage map (each test references a spec AC):
    test_create_empty_order                → AC-1            (req #1)
    test_create_order_with_two_lines       → AC-2, AC-3      (req #2)
    test_get_nonexistent_returns_404       → spec error 404  (req #3)
    test_close_already_closed_conflicts    → AC-6            (req #4)
    test_void_writes_reversing_movements   → AC-7            (req #5)
    test_idempotent_create_returns_same_id → AC-4            (req #6)
    test_float_money_rejected              → AC-12           (req #7)
    test_tw_invoice_fields_roundtrip       → AC-9, AC-10     (req #8)
    test_close_open_order_transitions      → AC-5            bonus
    test_void_preserves_closed_at          → AC-8            bonus
"""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from restaurant_api.api.deps import get_db
from restaurant_api.config import get_settings
from restaurant_api.main import app
from restaurant_api.models import (
    MovementType,
    Order,
    StockMovement,
)
from restaurant_api.models.orders import OrderStatus

# Importing the router has the side-effect of mounting it on
# ``restaurant_api.main.app``. We do this once at module import; the
# ``_mount`` helper is idempotent so this is safe across test runs.
importlib.import_module("restaurant_api.routers.orders")


# Override the session-scoped ``_engine`` fixture from conftest with a
# function-scoped one. pytest-asyncio 1.x rejects session-scoped async
# fixtures unless they declare ``loop_scope="session"``; the project's
# conftest hasn't been updated yet, and this module isn't allowed to
# touch it. Function-scope keeps the tests independent — engine creation
# is cheap relative to the savepoint+rollback round-trip.
@pytest_asyncio.fixture
async def _engine():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


# Override ``client`` from conftest. The shared fixture uses sync
# ``TestClient`` which runs the ASGI app on a separate event loop via
# anyio's blocking portal — that conflicts with the async DB session
# fixture's loop (asyncpg futures end up attached to the wrong loop). We
# swap in ``httpx.AsyncClient`` over ``ASGITransport`` so HTTP and DB
# share the single test loop.
@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    async def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ──────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────


def _err_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Pluck the inner error dict from whichever envelope shape FastAPI used.

    Our DomainError envelope: ``{"error": {"code": ..., "message": ...}}``.
    Default FastAPI HTTPException: ``{"detail": {"code": ..., "message": ...}}``.
    The test only cares about ``code`` and ``message``.
    """
    if "error" in body:
        return body["error"]
    if "detail" in body and isinstance(body["detail"], dict):
        return body["detail"]
    return body


def _base_create_body(store_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "store_id": str(store_id),
        "order_no": f"OD-{uuid.uuid4().hex[:8]}",
        "business_date": date.today().isoformat(),
    }
    body.update(overrides)
    return body


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


async def test_create_empty_order(client, db_session: AsyncSession, seed_store):
    """AC-1: minimal create returns 201 with id; no lines/movements written."""
    body = _base_create_body(seed_store.id)
    resp = await client.post("/orders", json=body)
    assert resp.status_code == 201, resp.text

    payload = resp.json()
    assert uuid.UUID(payload["id"])
    assert payload["status"] == "open"
    assert payload["lines"] == []
    assert payload["payments"] == []

    # Verify DB state through the same rolled-back session.
    n_orders = await db_session.execute(select(func.count()).select_from(Order))
    assert n_orders.scalar_one() == 1
    n_movements = await db_session.execute(
        select(func.count()).select_from(StockMovement),
    )
    assert n_movements.scalar_one() == 0


async def test_create_order_with_two_lines(
    client, db_session: AsyncSession, seed_store, seed_menu_item,
):
    """AC-2 + AC-3: two lines produce two movements; line_total computed."""
    body = _base_create_body(
        seed_store.id,
        lines=[
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "2",
                "unit_price": "150",
            },
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "1",
                "unit_price": "250",
            },
        ],
    )
    resp = await client.post("/orders", json=body)
    assert resp.status_code == 201, resp.text
    payload = resp.json()

    assert len(payload["lines"]) == 2
    line_totals = sorted(Decimal(ln["line_total"]) for ln in payload["lines"])
    assert line_totals == [Decimal("250.0000"), Decimal("300.0000")]

    # Phase-1 stub: one negative-qty SALE_CONSUME movement per line.
    mv_result = await db_session.execute(
        select(StockMovement).where(
            StockMovement.movement_type == MovementType.SALE_CONSUME,
        ),
    )
    movements = mv_result.scalars().all()
    assert len(movements) == 2
    for mv in movements:
        assert mv.qty < Decimal("0")
        assert mv.source_table == "order_lines"


async def test_get_nonexistent_returns_404(client):
    """GET /orders/{bogus_id} → 404 with structured error body."""
    bogus = uuid.uuid4()
    resp = await client.get(f"/orders/{bogus}")
    assert resp.status_code == 404
    err = _err_payload(resp.json())
    assert err.get("code") == "NOT_FOUND"
    assert "not found" in err.get("message", "").lower()


async def test_close_already_closed_conflicts(
    client, db_session: AsyncSession, seed_store, seed_menu_item,
):
    """AC-6: closing twice → 409 with conflict code."""
    body = _base_create_body(
        seed_store.id,
        lines=[
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "1",
                "unit_price": "100",
            },
        ],
    )
    created = await client.post("/orders", json=body)
    assert created.status_code == 201
    oid = created.json()["id"]

    first = await client.post(f"/orders/{oid}/close", json={})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "closed"
    assert first.json()["closed_at"] is not None

    second = await client.post(f"/orders/{oid}/close", json={})
    assert second.status_code == 409
    err = _err_payload(second.json())
    assert err.get("code") == "CONFLICT"


async def test_void_writes_reversing_movements(
    client, db_session: AsyncSession, seed_store, seed_menu_item,
):
    """AC-7: void produces reversing INSERTs; original rows untouched."""
    body = _base_create_body(
        seed_store.id,
        lines=[
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "1",
                "unit_price": "100",
            },
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "1",
                "unit_price": "200",
            },
        ],
    )
    created = await client.post("/orders", json=body)
    assert created.status_code == 201
    oid = created.json()["id"]

    # Snapshot the original movements before void.
    before_rows = (
        await db_session.execute(
            select(StockMovement.id, StockMovement.qty).where(
                StockMovement.movement_type == MovementType.SALE_CONSUME,
            ),
        )
    ).all()
    assert len(before_rows) == 2
    before_qtys = {r.id: r.qty for r in before_rows}

    void_resp = await client.post(f"/orders/{oid}/void", json={"reason": "wrong item"})
    assert void_resp.status_code == 200, void_resp.text
    assert void_resp.json()["status"] == "voided"

    # Original rows still there, qty unchanged.
    after_rows = (
        await db_session.execute(
            select(StockMovement.id, StockMovement.qty).where(
                StockMovement.id.in_(list(before_qtys.keys())),
            ),
        )
    ).all()
    assert len(after_rows) == 2
    for r in after_rows:
        assert r.qty == before_qtys[r.id]

    # Reversing rows are INSERTs (adjustment_in), one per original line.
    reversals = (
        await db_session.execute(
            select(StockMovement).where(
                StockMovement.movement_type == MovementType.ADJUSTMENT_IN,
                StockMovement.source_table == "order_voids",
            ),
        )
    ).scalars().all()
    assert len(reversals) == 2
    for rv in reversals:
        # Negated: original SALE_CONSUME qty was negative, so reversal is positive.
        assert rv.qty > Decimal("0")
        assert rv.source_id == uuid.UUID(oid)


async def test_idempotent_create_returns_same_id(
    client, db_session: AsyncSession, seed_store, seed_menu_item,
):
    """AC-4: repeating with the same external_pos_id replays, returns 200, no new rows."""
    pos_id = f"POS-{uuid.uuid4().hex[:10]}"
    body = _base_create_body(
        seed_store.id,
        external_pos_id=pos_id,
        pos_source="ichef",
        lines=[
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "1",
                "unit_price": "100",
            },
        ],
    )
    first = await client.post("/orders", json=body)
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    second = await client.post("/orders", json=body)
    assert second.status_code == 200, second.text  # replay, not 201
    assert second.json()["id"] == first_id

    # Exactly one order, exactly one sale_consume movement total.
    n_orders = (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar_one()
    assert n_orders == 1
    n_consumes = (
        await db_session.execute(
            select(func.count()).select_from(StockMovement).where(
                StockMovement.movement_type == MovementType.SALE_CONSUME,
            ),
        )
    ).scalar_one()
    assert n_consumes == 1


async def test_float_money_rejected(client, seed_store, seed_menu_item):
    """AC-12: strict-mode Pydantic rejects float literals on money fields."""
    body = _base_create_body(
        seed_store.id,
        lines=[
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": 1.5,  # bare float — must be rejected
                "unit_price": "100",
            },
        ],
    )
    resp = await client.post("/orders", json=body)
    assert resp.status_code == 422, resp.text


async def test_tw_invoice_fields_roundtrip(
    client, db_session: AsyncSession, seed_store,
):
    """AC-9 + AC-10: 統一發票 / 載具 / 統編 fields persist and round-trip."""
    body = _base_create_body(
        seed_store.id,
        invoice_number="AB12345678",
        carrier_type="mobile",
        carrier_id="/ABCD123",  # 8 chars, starts with /
        buyer_tax_id="12345678",
    )
    resp = await client.post("/orders", json=body)
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["invoice_number"] == "AB12345678"
    assert payload["carrier_type"] == "mobile"
    assert payload["carrier_id"] == "/ABCD123"
    assert payload["buyer_tax_id"] == "12345678"

    # GET round-trip
    get_resp = await client.get(f"/orders/{payload['id']}")
    assert get_resp.status_code == 200
    got = get_resp.json()
    assert got["invoice_number"] == "AB12345678"
    assert got["buyer_tax_id"] == "12345678"


async def test_close_open_order_transitions(
    client, seed_store, seed_menu_item,
):
    """AC-5: close → status=closed, closed_at populated."""
    body = _base_create_body(
        seed_store.id,
        lines=[
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "1",
                "unit_price": "100",
            },
        ],
    )
    created = await client.post("/orders", json=body)
    assert created.status_code == 201
    oid = created.json()["id"]

    closed_at = datetime.now(UTC).isoformat()
    resp = await client.post(f"/orders/{oid}/close", json={"closed_at": closed_at})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "closed"
    assert payload["closed_at"] is not None


async def test_void_preserves_closed_at(
    client, db_session: AsyncSession, seed_store, seed_menu_item,
):
    """AC-8: voiding a closed order keeps its closed_at; status becomes voided."""
    body = _base_create_body(
        seed_store.id,
        lines=[
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "1",
                "unit_price": "100",
            },
        ],
    )
    created = await client.post("/orders", json=body)
    oid = created.json()["id"]

    close_resp = await client.post(f"/orders/{oid}/close", json={})
    closed_at_before = close_resp.json()["closed_at"]
    assert closed_at_before is not None

    void_resp = await client.post(f"/orders/{oid}/void", json={"reason": "customer changed mind"})
    assert void_resp.status_code == 200, void_resp.text
    after = void_resp.json()
    assert after["status"] == "voided"
    assert after["closed_at"] == closed_at_before

    # Sanity check via the ORM too.
    row = (
        await db_session.execute(select(Order).where(Order.id == uuid.UUID(oid)))
    ).scalar_one()
    assert row.status == OrderStatus.VOIDED
    assert row.closed_at is not None


# Unused-import guard: pytest is referenced via the auto-applied asyncio mode.
_ = pytest
