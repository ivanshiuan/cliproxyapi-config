"""Integration tests for the /reports router (營運報表 + 後台匯出).

Drives real closed orders through the POS till (open → add → cash checkout) and
asserts the day-close report reconciles exactly with what was charged, plus the
top-items ranking, CSV export shape, and date-range validation.
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


async def _item(client: httpx.AsyncClient, store: str, sku: str, price: str) -> dict:
    r = await client.post(
        "/menu/items",
        json={"store_id": store, "sku": sku, "name": f"品項{sku}", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _sell(
    client: httpx.AsyncClient, store: str, table_name: str, lines: list[tuple[str, str]]
) -> dict:
    """Open a table, add (menu_item_id, qty) lines, cash-checkout. Return the
    checkout result."""
    t = (await client.post("/tables", json={"store_id": store, "name": table_name})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    for item_id, qty in lines:
        r = await client.post(
            f"/tables/sessions/{sess['id']}/lines",
            json={"menu_item_id": item_id, "qty": qty},
        )
        assert r.status_code == 201, r.text
    co = await client.post(
        f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "100000"}
    )
    assert co.status_code == 200, co.text
    return co.json()


async def test_daily_sales_reconciles_with_till(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "R1", "150")
    co = await _sell(client, store, "RT1", [(it["id"], "2")])  # net 300
    biz_date = co["order"]["business_date"]

    r = await client.get("/reports/sales", params={"store_id": store, "date_from": biz_date})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["order_count"] == 1
    assert Decimal(rep["gross_sales"]) == Decimal("300")
    assert Decimal(rep["net_sales"]) == Decimal("300")
    assert Decimal(rep["discount_total"]) == Decimal("0")
    assert Decimal(rep["item_count"]) == Decimal("2")
    assert Decimal(rep["avg_ticket"]) == Decimal("300")
    # payment breakdown: one cash payment of 300
    cash = next(p for p in rep["payments"] if p["method"] == "cash")
    assert cash["count"] == 1
    assert Decimal(cash["amount"]) == Decimal("300")


async def test_sales_sums_multiple_orders(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "R2", "100")
    co1 = await _sell(client, store, "RT2", [(it["id"], "1")])  # 100
    await _sell(client, store, "RT3", [(it["id"], "3")])  # 300
    biz_date = co1["order"]["business_date"]

    rep = (
        await client.get("/reports/sales", params={"store_id": store, "date_from": biz_date})
    ).json()
    assert rep["order_count"] == 2
    assert Decimal(rep["net_sales"]) == Decimal("400")
    assert Decimal(rep["avg_ticket"]) == Decimal("200")


async def test_top_items_ranked_by_qty(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    a = await _item(client, store, "TA", "50")
    b = await _item(client, store, "TB", "80")
    co = await _sell(client, store, "RT4", [(a["id"], "5"), (b["id"], "2")])
    biz_date = co["order"]["business_date"]

    rows = (
        await client.get(
            "/reports/top-items", params={"store_id": store, "date_from": biz_date}
        )
    ).json()
    assert [r["menu_item_id"] for r in rows][:2] == [a["id"], b["id"]]  # 5 qty > 2 qty
    top = rows[0]
    assert Decimal(top["qty"]) == Decimal("5")
    assert Decimal(top["revenue"]) == Decimal("250")
    assert top["name"] == "品項TA"


async def test_orders_csv_export(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "R3", "120")
    co = await _sell(client, store, "RT5", [(it["id"], "2")])  # 240
    biz_date = co["order"]["business_date"]

    r = await client.get(
        "/reports/export/orders.csv", params={"store_id": store, "date_from": biz_date}
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.text
    assert "order_no,business_date" in text  # header present
    assert co["order"]["order_no"] in text
    assert "240.00" in text  # net column (discount engine returns 2dp)
    assert "cash:240" in text  # payment breakdown column


async def test_empty_day_is_all_zero(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    rep = (
        await client.get(
            "/reports/sales", params={"store_id": store, "date_from": "2000-01-01"}
        )
    ).json()
    assert rep["order_count"] == 0
    assert Decimal(rep["net_sales"]) == Decimal("0")
    assert Decimal(rep["avg_ticket"]) == Decimal("0")
    assert rep["payments"] == []


async def test_bad_date_range_422(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    r = await client.get(
        "/reports/sales",
        params={"store_id": store, "date_from": "2026-07-10", "date_to": "2026-07-01"},
    )
    assert r.status_code == 422
