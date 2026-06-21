"""Customer loop tests for /orders close → points accrual + LINE push.

These exercise the path enabled by the customer_id FK on Order:
  - close writes a customer_points_ledger accrual row
  - close bumps Customer.points_balance / lifetime_value / last_visit_at
  - close pushes a LINE message when customer has line_user_id
  - tier multiplier is applied
  - no customer attached → close is unchanged (regression guard)
  - LINE failure does not block close
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db, get_line_messenger
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.main import app
from restaurant_api.models import (
    Customer,
    CustomerPointsLedger,
    CustomerTier,
    Order,
    Tenant,
)

# Side-effect import to make sure /orders is mounted (idempotent).
importlib.import_module("restaurant_api.routers.orders")

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def stub_messenger() -> StubLineMessenger:
    return StubLineMessenger()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    stub_messenger: StubLineMessenger,
) -> AsyncIterator[httpx.AsyncClient]:
    """Async client with tenant + messenger DI overrides."""

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> uuid.UUID:
        return seed_tenant.id

    def _override_messenger() -> StubLineMessenger:
        return stub_messenger

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    app.dependency_overrides[get_line_messenger] = _override_messenger
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seed_customer(
    db_session: AsyncSession, seed_tenant: Tenant
) -> Customer:
    c = Customer(
        tenant_id=seed_tenant.id,
        display_name="王大明",
        phone=f"0912{uuid.uuid4().hex[:6]}",
        line_user_id=f"U{uuid.uuid4().hex[:16]}",
        tier=CustomerTier.REGULAR,
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def gold_customer(
    db_session: AsyncSession, seed_tenant: Tenant
) -> Customer:
    c = Customer(
        tenant_id=seed_tenant.id,
        display_name="李黃金",
        phone=f"0928{uuid.uuid4().hex[:6]}",
        line_user_id=None,  # No LINE — confirms LINE is conditional
        tier=CustomerTier.GOLD,
    )
    db_session.add(c)
    await db_session.flush()
    return c


def _create_payload(
    store_id: uuid.UUID,
    menu_item_id: uuid.UUID,
    *,
    customer_id: uuid.UUID | None = None,
    unit_price: str = "1000.00",
    qty: str = "1",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "store_id": str(store_id),
        "order_no": f"C-{uuid.uuid4().hex[:8]}",
        "business_date": date.today().isoformat(),
        "opened_at": datetime.now(UTC).isoformat(),
        "lines": [
            {
                "menu_item_id": str(menu_item_id),
                "qty": qty,
                "unit_price": unit_price,
            }
        ],
    }
    if customer_id is not None:
        body["customer_id"] = str(customer_id)
    return body


# ──────────────────────────────────────────────────────────────────────────
# Create-time
# ──────────────────────────────────────────────────────────────────────────


async def test_order_carries_customer_id_on_create(
    client: httpx.AsyncClient,
    seed_store,
    seed_menu_item,
    seed_customer: Customer,
) -> None:
    resp = await client.post(
        "/orders",
        json=_create_payload(seed_store.id, seed_menu_item.id, customer_id=seed_customer.id),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["customer_id"] == str(seed_customer.id)


async def test_order_without_customer_id_returns_null(
    client: httpx.AsyncClient, seed_store, seed_menu_item
) -> None:
    resp = await client.post(
        "/orders", json=_create_payload(seed_store.id, seed_menu_item.id)
    )
    assert resp.status_code == 201
    assert resp.json()["customer_id"] is None


# ──────────────────────────────────────────────────────────────────────────
# Close → points
# ──────────────────────────────────────────────────────────────────────────


async def test_close_with_customer_writes_points_ledger_and_bumps_balance(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store,
    seed_menu_item,
    seed_customer: Customer,
) -> None:
    """1000 TWD net revenue * 1% * tier 1.0 = 10 points (REGULAR tier)."""
    create = await client.post(
        "/orders",
        json=_create_payload(
            seed_store.id, seed_menu_item.id, customer_id=seed_customer.id
        ),
    )
    order_id = create.json()["id"]
    close = await client.post(f"/orders/{order_id}/close", json={})
    assert close.status_code == 200, close.text
    assert close.json()["status"] == "closed"

    # One ledger row, +10 points.
    rows = (
        await db_session.execute(
            select(CustomerPointsLedger).where(
                CustomerPointsLedger.customer_id == seed_customer.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].delta == Decimal("10")
    assert rows[0].reason == "order.earn"
    assert rows[0].source_order_id == uuid.UUID(order_id)

    # Cached aggregates on the customer row updated.
    await db_session.refresh(seed_customer)
    assert seed_customer.points_balance == Decimal("10")
    assert seed_customer.lifetime_value == Decimal("1000.00")
    assert seed_customer.last_visit_at is not None


async def test_gold_tier_earns_2x_points(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store,
    seed_menu_item,
    gold_customer: Customer,
) -> None:
    """1000 TWD * 1% * 2.0 (GOLD) = 20 points."""
    create = await client.post(
        "/orders",
        json=_create_payload(
            seed_store.id, seed_menu_item.id, customer_id=gold_customer.id
        ),
    )
    await client.post(f"/orders/{create.json()['id']}/close", json={})

    await db_session.refresh(gold_customer)
    assert gold_customer.points_balance == Decimal("20")


async def test_close_without_customer_skips_points_path(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store,
    seed_menu_item,
) -> None:
    """Regression guard: existing dine-in path (no customer) still works,
    no ledger rows created."""
    create = await client.post(
        "/orders", json=_create_payload(seed_store.id, seed_menu_item.id)
    )
    close = await client.post(f"/orders/{create.json()['id']}/close", json={})
    assert close.status_code == 200

    # Scope to this tenant — a bare full-table scan trips over seed/demo or
    # other-tenant ledger rows (per the house rule in CLAUDE.md).
    n_ledger = (
        await db_session.execute(
            select(CustomerPointsLedger).where(
                CustomerPointsLedger.tenant_id == seed_store.tenant_id
            )
        )
    ).scalars().all()
    assert n_ledger == []


# ──────────────────────────────────────────────────────────────────────────
# LINE push
# ──────────────────────────────────────────────────────────────────────────


async def test_close_pushes_line_when_customer_has_line_user_id(
    client: httpx.AsyncClient,
    seed_store,
    seed_menu_item,
    seed_customer: Customer,
    stub_messenger: StubLineMessenger,
) -> None:
    create = await client.post(
        "/orders",
        json=_create_payload(
            seed_store.id, seed_menu_item.id, customer_id=seed_customer.id
        ),
    )
    await client.post(f"/orders/{create.json()['id']}/close", json={})

    # Exactly one push to this customer's LINE user.
    assert len(stub_messenger.sent_messages) == 1
    entry = stub_messenger.sent_messages[0]
    assert entry["op"] == "push"
    assert entry["to"] == seed_customer.line_user_id
    msg = entry["message"]
    assert "感謝您的光臨" in msg.text  # type: ignore[union-attr]
    assert "10" in msg.text  # type: ignore[union-attr]  # points earned


async def test_close_skips_line_when_customer_has_no_line_id(
    client: httpx.AsyncClient,
    seed_store,
    seed_menu_item,
    gold_customer: Customer,
    stub_messenger: StubLineMessenger,
) -> None:
    """gold_customer fixture has line_user_id=None — push must be skipped."""
    create = await client.post(
        "/orders",
        json=_create_payload(
            seed_store.id, seed_menu_item.id, customer_id=gold_customer.id
        ),
    )
    await client.post(f"/orders/{create.json()['id']}/close", json={})

    assert stub_messenger.sent_messages == []


async def test_close_succeeds_even_when_line_push_raises(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store,
    seed_menu_item,
    seed_customer: Customer,
    monkeypatch,
) -> None:
    """LINE outage / rate limit MUST NOT block order close."""
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("LINE down")

    # Swap the messenger DI for one whose .push throws.
    class BrokenMessenger(StubLineMessenger):
        push = _boom

    app.dependency_overrides[get_line_messenger] = lambda: BrokenMessenger()
    try:
        create = await client.post(
            "/orders",
            json=_create_payload(
                seed_store.id, seed_menu_item.id, customer_id=seed_customer.id
            ),
        )
        close = await client.post(f"/orders/{create.json()['id']}/close", json={})
        assert close.status_code == 200
        # Points still accrued — LINE failure isolated.
        await db_session.refresh(seed_customer)
        assert seed_customer.points_balance == Decimal("10")
    finally:
        # Restore — other tests in this module rely on the earlier override.
        # The client fixture's teardown will clear everything anyway.
        pass


# ──────────────────────────────────────────────────────────────────────────
# Order header sanity
# ──────────────────────────────────────────────────────────────────────────


async def test_order_row_has_customer_id_persisted(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store,
    seed_menu_item,
    seed_customer: Customer,
) -> None:
    """Belt-and-braces: the FK actually lands in the orders.customer_id column."""
    create = await client.post(
        "/orders",
        json=_create_payload(
            seed_store.id, seed_menu_item.id, customer_id=seed_customer.id
        ),
    )
    order_id = uuid.UUID(create.json()["id"])
    row = (
        await db_session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one()
    assert row.customer_id == seed_customer.id
