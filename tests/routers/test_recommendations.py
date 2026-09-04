"""P8.2 — 規則版智慧加購推薦 (/menu/recommendations): cart_pairing + top_seller."""

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


async def _item(client: httpx.AsyncClient, store: str, sku: str, price: str = "50") -> dict:
    r = await client.post(
        "/menu/items",
        json={"store_id": store, "sku": sku, "name": f"品項{sku}", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _sell(client: httpx.AsyncClient, store: str, table_name: str, item_ids: list[str]) -> None:
    """Open a table, add one line per item, cash-checkout (CLOSED order)."""
    t = (await client.post("/tables", json={"store_id": store, "name": table_name})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    for item_id in item_ids:
        r = await client.post(
            f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": item_id, "qty": "1"}
        )
        assert r.status_code == 201, r.text
    co = await client.post(f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "100000"})
    assert co.status_code == 200, co.text


async def test_no_history_returns_empty(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    r = await client.get("/menu/recommendations", params={"store_id": store})
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_cart_pairing_suggests_frequent_companion(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    beef = await _item(client, store, f"REC-BEEF-{uuid.uuid4().hex[:6]}")
    egg = await _item(client, store, f"REC-EGG-{uuid.uuid4().hex[:6]}")
    unrelated = await _item(client, store, f"REC-UNRELATED-{uuid.uuid4().hex[:6]}")

    for i in range(3):
        await _sell(client, store, f"RP{i}", [beef["id"], egg["id"]])
    await _sell(client, store, "RPU", [unrelated["id"]])

    r = await client.get(
        "/menu/recommendations", params={"store_id": store, "cart_item_ids": [beef["id"]]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body
    assert body[0]["menu_item_id"] == egg["id"]
    assert body[0]["reason"] == "cart_pairing"
    assert all(row["menu_item_id"] != beef["id"] for row in body)


async def test_no_cart_falls_back_to_top_seller(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    popular = await _item(client, store, f"REC-POP-{uuid.uuid4().hex[:6]}")
    rare = await _item(client, store, f"REC-RARE-{uuid.uuid4().hex[:6]}")

    for i in range(4):
        await _sell(client, store, f"RT{i}", [popular["id"]])
    await _sell(client, store, "RT-rare", [rare["id"]])

    r = await client.get("/menu/recommendations", params={"store_id": store})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body
    assert body[0]["menu_item_id"] == popular["id"]
    assert body[0]["reason"] == "top_seller"


async def test_unavailable_item_excluded(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    beef = await _item(client, store, f"REC-BEEF2-{uuid.uuid4().hex[:6]}")
    egg = await _item(client, store, f"REC-EGG2-{uuid.uuid4().hex[:6]}")
    for i in range(2):
        await _sell(client, store, f"RU{i}", [beef["id"], egg["id"]])

    off = await client.patch(f"/menu/items/{egg['id']}", json={"is_available": False})
    assert off.status_code == 200, off.text

    r = await client.get(
        "/menu/recommendations", params={"store_id": store, "cart_item_ids": [beef["id"]]}
    )
    assert r.status_code == 200, r.text
    assert all(row["menu_item_id"] != egg["id"] for row in r.json())


async def test_limit_honored(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    items = [await _item(client, store, f"REC-L-{uuid.uuid4().hex[:6]}") for _ in range(4)]
    await _sell(client, store, "RL0", [i["id"] for i in items])

    r = await client.get("/menu/recommendations", params={"store_id": store, "limit": 2})
    assert r.status_code == 200, r.text
    assert len(r.json()) <= 2
