"""Integration tests for customer table-QR ordering (/t/{qr_token}, P2).

The qr_token is the credential: everything is scoped to one table. Covers the
context payload (and that it never leaks operator fields like cost_estimate),
the staff-opens-first guard, cart submit with server prices + TABLE_QR channel,
the guest bill, and that a guest order fires the same real-time floor event the
staff POS listens to.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import Store, Tenant

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> uuid.UUID:
        return seed_tenant.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _setup(client: httpx.AsyncClient, store: str) -> tuple[dict, dict]:
    """Create a table (staff API) + one menu item → (table, item)."""
    t = (
        await client.post(
            "/tables", json={"store_id": store, "name": f"Q{uuid.uuid4().hex[:4]}"}
        )
    ).json()
    it = (
        await client.post(
            "/menu/items",
            json={
                "store_id": store,
                "sku": f"Q{uuid.uuid4().hex[:5]}",
                "name": "牛肉麵",
                "price": "160",
                "cost_estimate": "62",
            },
        )
    ).json()
    return t, it


async def test_context_hides_costs_and_flags_closed_table(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t, _it = await _setup(client, str(seed_store.id))
    r = await client.get(f"/t/{t['qr_token']}/context")
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["table_name"] == t["name"]
    assert ctx["is_open"] is False
    assert len(ctx["menu"]) >= 1
    m = ctx["menu"][0]
    # Guest-visible menu never carries operator fields.
    assert "cost_estimate" not in m and "sku" not in m
    assert {"id", "name", "price"} <= set(m.keys())


async def test_unknown_token_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/t/nosuchtoken123/context")
    assert r.status_code == 404


async def test_cart_requires_open_session_409(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t, it = await _setup(client, str(seed_store.id))
    r = await client.post(
        f"/t/{t['qr_token']}/cart",
        json={"items": [{"menu_item_id": it["id"], "qty": "1"}]},
    )
    assert r.status_code == 409


async def test_cart_submit_appends_to_bill_with_server_prices(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t, it = await _setup(client, store)
    await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})

    r = await client.post(
        f"/t/{t['qr_token']}/cart",
        json={"items": [{"menu_item_id": it["id"], "qty": "2"}]},
    )
    assert r.status_code == 200, r.text
    bill = r.json()
    assert bill["table_name"] == t["name"]
    assert len(bill["lines"]) == 1
    assert bill["lines"][0]["name"] == "牛肉麵"
    assert Decimal(bill["lines"][0]["line_total"]) == Decimal("320")  # 2 x server 160
    assert Decimal(bill["total"]) == Decimal("320")


async def test_guest_order_is_channel_table_qr_and_staff_sees_it(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t, it = await _setup(client, store)
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()

    await client.post(
        f"/t/{t['qr_token']}/cart",
        json={"items": [{"menu_item_id": it["id"], "qty": "1"}]},
    )
    # Staff POS sees the same order on the session, stamped table_qr.
    order = (await client.get(f"/tables/sessions/{sess['id']}/order")).json()
    assert order["channel"] == "table_qr"
    assert len(order["lines"]) == 1
    # And the real-time floor stream carries the add (what makes it pop on POS).
    evs = (
        await client.get("/tables/events", params={"store_id": store, "after": 0})
    ).json()
    assert "order.line_added" in [e["type"] for e in evs]


async def test_guest_and_staff_share_one_bill(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t, it = await _setup(client, store)
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()

    # Staff adds first (channel decided by first add → pos), guest adds after.
    await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it["id"], "qty": "1"},
    )
    r = await client.post(
        f"/t/{t['qr_token']}/cart",
        json={"items": [{"menu_item_id": it["id"], "qty": "1"}]},
    )
    bill = r.json()
    assert len(bill["lines"]) == 2
    assert Decimal(bill["total"]) == Decimal("320")


async def test_bill_on_closed_table_is_empty(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t, _it = await _setup(client, str(seed_store.id))
    r = await client.get(f"/t/{t['qr_token']}/bill")
    assert r.status_code == 200
    assert r.json()["lines"] == []
    assert Decimal(r.json()["total"]) == Decimal("0")
