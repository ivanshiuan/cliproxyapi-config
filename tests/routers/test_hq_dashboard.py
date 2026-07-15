"""P8.3 — 總部多店儀表板 (/reports/hq): multi-store rollup + ranking."""

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


async def _item(client: httpx.AsyncClient, store: str, sku: str, price: str) -> dict:
    r = await client.post(
        "/menu/items", json={"store_id": store, "sku": sku, "name": f"品項{sku}", "price": price}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _sell(
    client: httpx.AsyncClient, store: str, table_name: str, lines: list[tuple[str, str]]
) -> dict:
    t = (await client.post("/tables", json={"store_id": store, "name": table_name})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    for item_id, qty in lines:
        r = await client.post(
            f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": item_id, "qty": qty}
        )
        assert r.status_code == 201, r.text
    co = await client.post(f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "100000"})
    assert co.status_code == 200, co.text
    return co.json()


async def test_no_stores_returns_empty(client: httpx.AsyncClient, seed_tenant: Tenant) -> None:
    r = await client.get("/reports/hq", params={"date_from": "2026-01-01"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["store_count"] == 0
    assert body["stores"] == []
    assert Decimal(body["total_net_sales"]) == Decimal("0")


async def test_rolls_up_and_ranks_multiple_stores(
    client: httpx.AsyncClient, seed_store: Store, db_session: AsyncSession
) -> None:
    other_store = Store(tenant_id=seed_store.tenant_id, name="Branch 2", is_active=True)
    db_session.add(other_store)
    await db_session.flush()

    it_a = await _item(client, str(seed_store.id), f"HQ-A-{uuid.uuid4().hex[:6]}", "100")
    it_b = await _item(client, str(other_store.id), f"HQ-B-{uuid.uuid4().hex[:6]}", "300")

    co_a = await _sell(client, str(seed_store.id), "HQA1", [(it_a["id"], "1")])
    co_b = await _sell(client, str(other_store.id), "HQB1", [(it_b["id"], "1")])
    biz_date = co_a["order"]["business_date"]
    assert co_b["order"]["business_date"] == biz_date

    r = await client.get("/reports/hq", params={"date_from": biz_date})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["store_count"] == 2
    assert body["total_order_count"] == 2
    assert Decimal(body["total_net_sales"]) == Decimal("400")

    names = [s["store_name"] for s in body["stores"]]
    assert names == ["Branch 2", seed_store.name]  # ranked by net_sales desc: 300 > 100
    assert Decimal(body["stores"][0]["net_sales"]) == Decimal("300")
    assert Decimal(body["stores"][1]["net_sales"]) == Decimal("100")


async def test_store_with_no_sales_still_listed_at_zero(
    client: httpx.AsyncClient, seed_store: Store, db_session: AsyncSession
) -> None:
    quiet_store = Store(tenant_id=seed_store.tenant_id, name="Quiet Branch", is_active=True)
    db_session.add(quiet_store)
    await db_session.flush()

    it = await _item(client, str(seed_store.id), f"HQ-C-{uuid.uuid4().hex[:6]}", "50")
    co = await _sell(client, str(seed_store.id), "HQC1", [(it["id"], "1")])

    r = await client.get("/reports/hq", params={"date_from": co["order"]["business_date"]})
    assert r.status_code == 200, r.text
    body = r.json()

    quiet_row = next(s for s in body["stores"] if s["store_name"] == "Quiet Branch")
    assert quiet_row["order_count"] == 0
    assert Decimal(quiet_row["net_sales"]) == Decimal("0")


async def test_inactive_store_excluded(
    client: httpx.AsyncClient, seed_store: Store, db_session: AsyncSession
) -> None:
    closed_store = Store(tenant_id=seed_store.tenant_id, name="Closed Branch", is_active=False)
    db_session.add(closed_store)
    await db_session.flush()

    r = await client.get("/reports/hq", params={"date_from": "2026-01-01"})
    assert r.status_code == 200, r.text
    names = [s["store_name"] for s in r.json()["stores"]]
    assert "Closed Branch" not in names
