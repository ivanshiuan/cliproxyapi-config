"""P7.5 — 線上外帶 (order-ahead pickup, no table, pay-at-counter)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
    db_session: AsyncSession, seed_tenant: Tenant
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


async def _item(client: httpx.AsyncClient, store: str, price: str) -> dict:
    r = await client.post(
        "/menu/items",
        json={"store_id": store, "sku": f"OT{uuid.uuid4().hex[:6]}", "name": "外帶品項", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_online_takeout_stays_open_no_payment(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "80")
    r = await client.post(
        "/online-takeout",
        json={
            "store_id": store,
            "contact_name": "陳先生",
            "contact_phone": "0912345678",
            "items": [{"menu_item_id": it["id"], "qty": "2"}],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert Decimal(body["estimated_total"]) == Decimal("160")
    assert body["order"]["status"] == "open"
    assert body["order"]["channel"] == "online"
    assert body["order"]["order_type"] == "takeout"


async def test_pending_list_and_collect(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "50")
    created = (
        await client.post(
            "/online-takeout",
            json={
                "store_id": store, "contact_name": "林小姐", "contact_phone": "0987654321",
                "items": [{"menu_item_id": it["id"], "qty": "3"}],
            },
        )
    ).json()
    order_id = created["order"]["id"]

    pending = (await client.get("/online-takeout/pending", params={"store_id": store})).json()
    assert any(o["id"] == order_id for o in pending)

    co = await client.post(f"/online-takeout/{order_id}/collect", json={"tendered": "150"})
    assert co.status_code == 200, co.text
    result = co.json()
    assert Decimal(result["total"]) == Decimal("150")
    assert Decimal(result["change"]) == Decimal("0")
    assert result["order"]["status"] == "closed"

    pending_after = (await client.get("/online-takeout/pending", params={"store_id": store})).json()
    assert all(o["id"] != order_id for o in pending_after)


async def test_collect_insufficient_cash_rejected(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "100")
    created = (
        await client.post(
            "/online-takeout",
            json={
                "store_id": store, "contact_name": "王先生", "contact_phone": "0911111111",
                "items": [{"menu_item_id": it["id"], "qty": "1"}],
            },
        )
    ).json()
    order_id = created["order"]["id"]
    r = await client.post(f"/online-takeout/{order_id}/collect", json={"tendered": "50"})
    assert r.status_code == 422, r.text


async def test_collect_wrong_order_type_404(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "60")
    # A normal dine-in order should NOT be collectible via the online-takeout endpoint.
    t = (await client.post("/tables", json={"store_id": store, "name": f"OT-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"})
    order_resp = await client.get(f"/tables/sessions/{sess['id']}/order")
    order_id = order_resp.json()["id"]
    r = await client.post(f"/online-takeout/{order_id}/collect", json={"tendered": "1000"})
    assert r.status_code == 404, r.text


async def test_pickup_at_and_notes_persisted(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "40")
    pickup = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/online-takeout",
        json={
            "store_id": store, "contact_name": "張小姐", "contact_phone": "0922222222",
            "items": [{"menu_item_id": it["id"], "qty": "1"}], "pickup_at": pickup,
            "notes": "不要香菜",
        },
    )
    assert r.status_code == 201, r.text
    order = r.json()["order"]
    assert order["notes"] is not None and "張小姐" in order["notes"]
