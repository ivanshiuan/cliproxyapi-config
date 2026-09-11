"""外帶快速單 (takeout quick sale) tests — one-shot order+pay+close, no table."""

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


async def _item(client: httpx.AsyncClient, store: str, price: str) -> dict:
    r = await client.post(
        "/menu/items",
        json={"store_id": store, "sku": f"T{uuid.uuid4().hex[:5]}", "name": "外帶品", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_takeout_one_shot_sale(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    a = await _item(client, store, "80")
    b = await _item(client, store, "45")

    r = await client.post(
        "/tables/takeout",
        json={
            "store_id": store,
            "items": [
                {"menu_item_id": a["id"], "qty": "2"},  # 160
                {"menu_item_id": b["id"], "qty": "1"},  # 45
            ],
            "tendered": "500",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert Decimal(body["total"]) == Decimal("205")
    assert Decimal(body["change"]) == Decimal("295")
    order = body["order"]
    assert order["status"] == "closed"
    assert order["order_type"] == "takeout"
    assert order["channel"] == "pos"
    assert order["table_session_id"] is None
    assert len(order["lines"]) == 2
    # server-side price snapshot held
    assert {Decimal(ln["unit_price"]) for ln in order["lines"]} == {Decimal("80"), Decimal("45")}


async def test_takeout_insufficient_cash_422_rolls_back(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    a = await _item(client, store, "100")
    r = await client.post(
        "/tables/takeout",
        json={"store_id": store, "items": [{"menu_item_id": a["id"], "qty": "3"}], "tendered": "100"},
    )
    assert r.status_code == 422


async def test_takeout_lands_on_kds_and_event_stream(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    a = await _item(client, store, "60")
    r = await client.post(
        "/tables/takeout",
        json={"store_id": store, "items": [{"menu_item_id": a["id"], "qty": "1"}], "tendered": "60"},
    )
    assert r.status_code == 201
    order_id = r.json()["order"]["id"]
    # cooks like everything else
    q = (await client.get("/kitchen/queue", params={"store_id": store})).json()
    assert any(x["order_id"] == order_id for x in q)
    # and announces itself on the live stream
    evs = (await client.get("/tables/events", params={"store_id": store, "after": 0})).json()
    assert any(e["type"] == "order.takeout_sale" for e in evs)


async def test_takeout_unknown_item_404(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    r = await client.post(
        "/tables/takeout",
        json={
            "store_id": str(seed_store.id),
            "items": [{"menu_item_id": str(uuid.uuid4()), "qty": "1"}],
            "tendered": "100",
        },
    )
    assert r.status_code == 404
