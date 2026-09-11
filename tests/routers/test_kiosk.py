"""P9.1 — Kiosk 自助點餐 (docs/23 G1): source=kiosk on the online-takeout flow."""

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


async def _item(client: httpx.AsyncClient, store: str, price: str = "80") -> dict:
    r = await client.post(
        "/menu/items",
        json={"store_id": store, "sku": f"KI{uuid.uuid4().hex[:8]}", "name": "品項", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_kiosk_order_no_contact_needed(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "120")
    r = await client.post(
        "/online-takeout",
        json={"store_id": store, "source": "kiosk", "items": [{"menu_item_id": it["id"], "qty": "1"}]},
    )
    assert r.status_code == 201, r.text
    order = r.json()["order"]
    assert order["channel"] == "tablet"
    assert order["order_no"].startswith("KS-")
    assert Decimal(r.json()["estimated_total"]) == Decimal("120")


async def test_online_source_still_requires_contact(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store)
    r = await client.post(
        "/online-takeout",
        json={"store_id": store, "items": [{"menu_item_id": it["id"], "qty": "1"}]},
    )
    assert r.status_code == 422, r.text  # no contact info on a remote order


async def test_kiosk_order_shows_on_pending_and_collectable(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "60")
    created = (
        await client.post(
            "/online-takeout",
            json={"store_id": store, "source": "kiosk", "items": [{"menu_item_id": it["id"], "qty": "1"}]},
        )
    ).json()
    order_id = created["order"]["id"]

    pending = (await client.get("/online-takeout/pending", params={"store_id": store})).json()
    assert any(o["id"] == order_id for o in pending)

    call = await client.post(f"/online-takeout/{order_id}/call")
    assert call.status_code == 200, call.text
    board = (await client.get("/online-takeout/board", params={"store_id": store})).json()
    assert any(e["order_no"] == created["order"]["order_no"] for e in board)

    collect = await client.post(f"/online-takeout/{order_id}/collect", json={"tendered": "60"})
    assert collect.status_code == 200, collect.text
    assert Decimal(collect.json()["total"]) == Decimal("60")

    pending_after = (await client.get("/online-takeout/pending", params={"store_id": store})).json()
    assert all(o["id"] != order_id for o in pending_after)


async def test_kiosk_and_online_orders_coexist_on_panel(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store)
    kiosk = (
        await client.post(
            "/online-takeout",
            json={"store_id": store, "source": "kiosk", "items": [{"menu_item_id": it["id"], "qty": "1"}]},
        )
    ).json()
    online = (
        await client.post(
            "/online-takeout",
            json={
                "store_id": store,
                "contact_name": "遠端客",
                "contact_phone": "0911222333",
                "items": [{"menu_item_id": it["id"], "qty": "1"}],
            },
        )
    ).json()
    pending = (await client.get("/online-takeout/pending", params={"store_id": store})).json()
    ids = {o["id"] for o in pending}
    assert kiosk["order"]["id"] in ids and online["order"]["id"] in ids
    assert online["order"]["order_no"].startswith("OT-")
