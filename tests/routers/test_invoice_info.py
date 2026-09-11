"""P7.5 — 發票資訊登錄 (🟡 partial e-invoice: capture 統編/載具 at checkout)."""

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
        json={"store_id": store, "sku": f"INV{uuid.uuid4().hex[:6]}", "name": "發票品項", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_checkout_records_buyer_tax_id(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "100")
    t = (await client.post("/tables", json={"store_id": store, "name": f"INV-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"})

    co = await client.post(
        f"/tables/sessions/{sess['id']}/checkout",
        json={"tendered": "100", "buyer_tax_id": "12345678"},
    )
    assert co.status_code == 200, co.text
    assert co.json()["order"]["buyer_tax_id"] == "12345678"


async def test_checkout_records_mobile_carrier(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "50")
    t = (await client.post("/tables", json={"store_id": store, "name": f"INV-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"})

    co = await client.post(
        f"/tables/sessions/{sess['id']}/checkout",
        json={"tendered": "50", "carrier_type": "mobile", "carrier_id": "/ABC1234"},
    )
    assert co.status_code == 200, co.text
    order = co.json()["order"]
    assert order["carrier_type"] == "mobile"
    assert order["carrier_id"] == "/ABC1234"


async def test_invalid_mobile_carrier_rejected(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "50")
    t = (await client.post("/tables", json={"store_id": store, "name": f"INV-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"})

    co = await client.post(
        f"/tables/sessions/{sess['id']}/checkout",
        json={"tendered": "50", "carrier_type": "mobile", "carrier_id": "bad"},
    )
    assert co.status_code == 422, co.text


async def test_invalid_buyer_tax_id_rejected(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "50")
    t = (await client.post("/tables", json={"store_id": store, "name": f"INV-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"})

    co = await client.post(
        f"/tables/sessions/{sess['id']}/checkout",
        json={"tendered": "50", "buyer_tax_id": "123"},  # not 8 digits
    )
    assert co.status_code == 422, co.text


async def test_takeout_sale_records_invoice_info(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "60")
    r = await client.post(
        "/tables/takeout",
        json={
            "store_id": store, "items": [{"menu_item_id": it["id"], "qty": "1"}], "tendered": "60",
            "buyer_tax_id": "87654321",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["order"]["buyer_tax_id"] == "87654321"
