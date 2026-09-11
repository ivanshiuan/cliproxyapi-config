"""P7.4 — 報表擴充: 時段分析 (/reports/hourly) + 通路分佈 (/reports/channels)."""

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
        json={"store_id": store, "sku": f"RT{uuid.uuid4().hex[:6]}", "name": "報表品項", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _dine_in_sale(client: httpx.AsyncClient, store: str, item_id: str, qty: str) -> dict:
    t = (await client.post("/tables", json={"store_id": store, "name": f"RT-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": item_id, "qty": qty}
    )
    assert r.status_code == 201, r.text
    co = await client.post(f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "100000"})
    assert co.status_code == 200, co.text
    return co.json()


async def test_hourly_report_buckets_current_hour(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "100")
    co1 = await _dine_in_sale(client, store, it["id"], "1")  # 100
    await _dine_in_sale(client, store, it["id"], "2")  # 200
    biz_date = co1["order"]["business_date"]

    r = await client.get("/reports/hourly", params={"store_id": store, "date_from": biz_date})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["slices"]) == 24
    nonzero = [s for s in body["slices"] if s["order_count"] > 0]
    assert len(nonzero) == 1  # both sales landed in the same (current) hour
    assert nonzero[0]["order_count"] == 2
    assert Decimal(nonzero[0]["net_sales"]) == Decimal("300")
    # every other hour is a zero-filled row, not just absent
    zero_hours = {s["hour"] for s in body["slices"]} - {nonzero[0]["hour"]}
    assert len(zero_hours) == 23


async def test_hourly_report_empty_range_all_zero(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    r = await client.get(
        "/reports/hourly", params={"store_id": store, "date_from": "2000-01-01"}
    )
    body = r.json()
    assert len(body["slices"]) == 24
    assert all(s["order_count"] == 0 for s in body["slices"])
    assert all(Decimal(s["net_sales"]) == Decimal("0") for s in body["slices"])


async def test_channel_report_dine_in_vs_takeout_share(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "100")
    co1 = await _dine_in_sale(client, store, it["id"], "1")  # dine_in: 100
    biz_date = co1["order"]["business_date"]

    to = await client.post(
        "/tables/takeout",
        json={"store_id": store, "items": [{"menu_item_id": it["id"], "qty": "3"}], "tendered": "300"},
    )
    assert to.status_code == 201, to.text  # takeout: 300

    r = await client.get("/reports/channels", params={"store_id": store, "date_from": biz_date})
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["net_sales"]) == Decimal("400")
    by_type = {s["order_type"]: s for s in body["slices"]}
    assert by_type["dine_in"]["order_count"] == 1
    assert Decimal(by_type["dine_in"]["net_sales"]) == Decimal("100")
    assert Decimal(by_type["dine_in"]["share"]) == Decimal("0.2500")
    assert by_type["takeout"]["order_count"] == 1
    assert Decimal(by_type["takeout"]["net_sales"]) == Decimal("300")
    assert Decimal(by_type["takeout"]["share"]) == Decimal("0.7500")


async def test_channel_report_empty_range_all_zero(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    r = await client.get(
        "/reports/channels", params={"store_id": store, "date_from": "2000-01-01"}
    )
    body = r.json()
    assert Decimal(body["net_sales"]) == Decimal("0")
    assert body["slices"] == []
