"""P7 補債 #10 — 自動折扣規則 (滿額/時段自動套) integration tests.

Covers the rule catalog CRUD plus the auto-apply pass in the POS money
paths: quote previews it, checkout charges it, the rule applies exactly
once, and partial-paid bills are never repriced.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import Store, Tenant

pytestmark = pytest.mark.asyncio

_TPE = ZoneInfo("Asia/Taipei")


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
        json={"store_id": store, "sku": f"DR{uuid.uuid4().hex[:8]}", "name": "品項", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _open_with_lines(
    client: httpx.AsyncClient, store: str, item_id: str, qty: str
) -> str:
    """Open a table, add one line, return session_id."""
    t = (
        await client.post("/tables", json={"store_id": store, "name": f"DR-{uuid.uuid4().hex[:6]}"})
    ).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": item_id, "qty": qty}
    )
    assert r.status_code == 201, r.text
    return sess["id"]


def _now_window(offset_start_min: int, offset_end_min: int) -> tuple[str, str]:
    """A Taipei-time window positioned relative to now (minutes)."""
    now = datetime.now(_TPE)
    start = (now + timedelta(minutes=offset_start_min)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(minutes=offset_end_min)).time().replace(second=0, microsecond=0)
    return start.isoformat(), end.isoformat()


# ── CRUD + validation ──────────────────────────────────────────────────────


async def test_create_list_delete_rule(client: httpx.AsyncClient, seed_store: Store) -> None:
    r = await client.post(
        "/discount-rules",
        json={"name": "滿千折百", "kind": "amount", "value": "100", "min_subtotal": "1000"},
    )
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["name"] == "滿千折百"

    listed = (await client.get("/discount-rules")).json()
    assert any(x["id"] == rule["id"] for x in listed)

    off = await client.patch(f"/discount-rules/{rule['id']}", json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert all(x["id"] != rule["id"] for x in (await client.get("/discount-rules")).json())

    d = await client.delete(f"/discount-rules/{rule['id']}")
    assert d.status_code == 204


async def test_validation_rejects_bad_rules(client: httpx.AsyncClient) -> None:
    over_one = await client.post(
        "/discount-rules", json={"name": "x", "kind": "percent", "value": "1.5"}
    )
    assert over_one.status_code == 422

    half_window = await client.post(
        "/discount-rules",
        json={"name": "x", "kind": "amount", "value": "10", "start_time": "14:00:00"},
    )
    assert half_window.status_code == 422

    float_money = await client.post(
        "/discount-rules", json={"name": "x", "kind": "amount", "value": 10.5}
    )
    assert float_money.status_code == 422


# ── auto-apply in the money paths ──────────────────────────────────────────


async def test_min_subtotal_rule_applies_at_checkout(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    await client.post(
        "/discount-rules",
        json={"name": "滿200打9折", "kind": "percent", "value": "0.1", "min_subtotal": "200", "store_id": store},
    )
    it = await _item(client, store, "150")
    sess = await _open_with_lines(client, store, it["id"], "2")  # gross 300 >= 200

    q = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert Decimal(q["net"]) == Decimal("270")  # 300 - 10%
    assert q["auto_discount"] == "自動折扣: 滿200打9折"

    co = await client.post(f"/tables/sessions/{sess}/checkout", json={"tendered": "270"})
    assert co.status_code == 200, co.text
    assert Decimal(co.json()["total"]) == Decimal("270")
    assert Decimal(co.json()["change"]) == Decimal("0")


async def test_below_threshold_not_applied(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    await client.post(
        "/discount-rules",
        json={"name": "滿500折50", "kind": "amount", "value": "50", "min_subtotal": "500", "store_id": store},
    )
    it = await _item(client, store, "100")
    sess = await _open_with_lines(client, store, it["id"], "1")  # gross 100 < 500

    q = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert Decimal(q["net"]) == Decimal("100")
    assert q["auto_discount"] is None


async def test_time_window_rule(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    in_start, in_end = _now_window(-30, 30)
    out_start, out_end = _now_window(60, 120)
    await client.post(
        "/discount-rules",
        json={"name": "時段外", "kind": "amount", "value": "99", "start_time": out_start, "end_time": out_end, "store_id": store},
    )
    it = await _item(client, store, "100")
    sess = await _open_with_lines(client, store, it["id"], "1")
    q = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert q["auto_discount"] is None, "rule outside its daily window must not apply"

    await client.post(
        "/discount-rules",
        json={"name": "快樂時光折20", "kind": "amount", "value": "20", "start_time": in_start, "end_time": in_end, "store_id": store},
    )
    sess2 = await _open_with_lines(client, store, it["id"], "1")
    q2 = (await client.get(f"/tables/sessions/{sess2}/quote")).json()
    assert Decimal(q2["net"]) == Decimal("80")
    assert q2["auto_discount"] == "自動折扣: 快樂時光折20"


async def test_best_of_multiple_rules_and_single_application(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    await client.post(
        "/discount-rules",
        json={"name": "小折", "kind": "amount", "value": "10", "min_subtotal": "100", "store_id": store},
    )
    await client.post(
        "/discount-rules",
        json={"name": "大折", "kind": "percent", "value": "0.2", "min_subtotal": "100", "store_id": store},
    )
    it = await _item(client, store, "200")
    sess = await _open_with_lines(client, store, it["id"], "1")

    # quote twice then checkout — the rule must land exactly once (the bigger one)
    q1 = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    q2 = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert q1["auto_discount"] == "自動折扣: 大折"
    assert Decimal(q1["net"]) == Decimal(q2["net"]) == Decimal("160")

    co = await client.post(f"/tables/sessions/{sess}/checkout", json={"tendered": "160"})
    assert co.status_code == 200, co.text
    order = co.json()["order"]
    rule_discounts = [d for d in order["discounts"]]
    assert len(rule_discounts) == 1


async def test_partial_paid_order_not_repriced(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "300")
    sess = await _open_with_lines(client, store, it["id"], "1")

    pay = await client.post(
        f"/tables/sessions/{sess}/pay", json={"amount": "100", "tendered": "100"}
    )
    assert pay.status_code == 200, pay.text

    # rule created AFTER the first partial payment — the bill must not change
    await client.post(
        "/discount-rules",
        json={"name": "遲到的折扣", "kind": "percent", "value": "0.5", "min_subtotal": "100", "store_id": store},
    )
    q = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert q["auto_discount"] is None
    assert Decimal(q["remaining"]) == Decimal("200")

    co = await client.post(f"/tables/sessions/{sess}/checkout", json={"tendered": "200"})
    assert co.status_code == 200, co.text
    assert Decimal(co.json()["total"]) == Decimal("200")


async def test_takeout_sale_applies_rule(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    await client.post(
        "/discount-rules",
        json={"name": "外帶滿百折10", "kind": "amount", "value": "10", "min_subtotal": "100", "store_id": store},
    )
    it = await _item(client, store, "120")
    r = await client.post(
        "/tables/takeout",
        json={"store_id": store, "items": [{"menu_item_id": it["id"], "qty": "1"}], "tendered": "110"},
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["total"]) == Decimal("110")


async def test_tenant_wide_rule_applies_to_any_store(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    await client.post(
        "/discount-rules",
        json={"name": "全品牌折5", "kind": "amount", "value": "5", "min_subtotal": "50"},  # no store_id
    )
    it = await _item(client, store, "60")
    sess = await _open_with_lines(client, store, it["id"], "1")
    q = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert Decimal(q["net"]) == Decimal("55")
