"""P8.1 — 取餐叫號看板 (public pickup-call display for 線上外帶)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

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
        json={"store_id": store, "sku": f"PB{uuid.uuid4().hex[:6]}", "name": "叫號品項", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _create_online_takeout(client: httpx.AsyncClient, store: str, price: str) -> dict:
    it = await _item(client, store, price)
    r = await client.post(
        "/online-takeout",
        json={
            "store_id": store, "contact_name": "取餐客", "contact_phone": "0911111111",
            "items": [{"menu_item_id": it["id"], "qty": "1"}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["order"]


async def test_call_for_pickup_appears_on_board(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    order = await _create_online_takeout(client, store, "60")

    r = await client.post(f"/online-takeout/{order['id']}/call")
    assert r.status_code == 200, r.text
    assert r.json()["pickup_called_at"] is not None

    board = (await client.get("/online-takeout/board", params={"store_id": store})).json()
    assert any(e["order_no"] == order["order_no"] for e in board)


async def test_board_newest_call_first(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    order1 = await _create_online_takeout(client, store, "50")
    order2 = await _create_online_takeout(client, store, "50")

    await client.post(f"/online-takeout/{order1['id']}/call")
    await client.post(f"/online-takeout/{order2['id']}/call")

    board = (await client.get("/online-takeout/board", params={"store_id": store})).json()
    order_nos = [e["order_no"] for e in board]
    assert order_nos.index(order2["order_no"]) < order_nos.index(order1["order_no"])


async def test_collecting_removes_from_board(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    order = await _create_online_takeout(client, store, "60")
    await client.post(f"/online-takeout/{order['id']}/call")

    co = await client.post(f"/online-takeout/{order['id']}/collect", json={"tendered": "60"})
    assert co.status_code == 200, co.text

    board = (await client.get("/online-takeout/board", params={"store_id": store})).json()
    assert all(e["order_no"] != order["order_no"] for e in board)


async def test_calling_non_takeout_order_rejected(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "40")
    t = (await client.post("/tables", json={"store_id": store, "name": f"PB-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"})
    order = (await client.get(f"/tables/sessions/{sess['id']}/order")).json()

    r = await client.post(f"/online-takeout/{order['id']}/call")
    assert r.status_code == 404, r.text


async def test_board_empty_when_nothing_called(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    board = (await client.get("/online-takeout/board", params={"store_id": store})).json()
    assert board == []
