"""P7.1 billing tests — 服務費, 招待 (comp), 拆單/分開結帳 (partial payments).

All money paths go through ``pos_order_service.amount_due`` — these tests pin
that quote, checkout, partial payments, and the sales report all agree.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.api.pos_auth import PosPrincipal, get_pos_principal
from restaurant_api.main import app
from restaurant_api.models import Employee, EmployeeRole, Store, Tenant

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> uuid.UUID:
        return seed_tenant.id

    # Real manager employee so gated ops (折扣/招待) audit against a valid actor.
    manager = Employee(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        full_name="帳務經理",
        role=EmployeeRole.MANAGER,
        hourly_wage=Decimal("200"),
        hired_on=date(2026, 1, 1),
    )
    db_session.add(manager)
    await db_session.flush()

    def _override_principal() -> PosPrincipal:
        return PosPrincipal(
            employee_id=manager.id, store_id=seed_store.id, role=EmployeeRole.MANAGER
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    app.dependency_overrides[get_pos_principal] = _override_principal
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _open_with_lines(
    client: httpx.AsyncClient, store: str, price: str, qty: str
) -> str:
    """Table + item + open + one line → session_id."""
    t = (
        await client.post(
            "/tables", json={"store_id": store, "name": f"B{uuid.uuid4().hex[:4]}"}
        )
    ).json()
    it = (
        await client.post(
            "/menu/items",
            json={"store_id": store, "sku": f"B{uuid.uuid4().hex[:5]}", "name": "品", "price": price},
        )
    ).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it["id"], "qty": qty},
    )
    assert r.status_code == 201, r.text
    return sess["id"]


# ── 服務費 ────────────────────────────────────────────────────────────────────


async def test_service_charge_flows_into_quote_checkout_and_report(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    sess = await _open_with_lines(client, store, "100", "2")  # gross 200

    q0 = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert Decimal(q0["service_charge"]) == Decimal("0")
    assert Decimal(q0["net"]) == Decimal("200")

    r = await client.post(
        f"/tables/sessions/{sess}/service-charge", json={"rate": "0.1"}
    )
    assert r.status_code == 200, r.text
    q = r.json()
    assert Decimal(q["service_charge"]) == Decimal("20")
    assert Decimal(q["net"]) == Decimal("220")
    assert Decimal(q["remaining"]) == Decimal("220")

    co = await client.post(f"/tables/sessions/{sess}/checkout", json={"tendered": "300"})
    assert co.status_code == 200, co.text
    assert Decimal(co.json()["total"]) == Decimal("220")
    assert Decimal(co.json()["change"]) == Decimal("80")

    biz = co.json()["order"]["business_date"]
    rep = (
        await client.get("/reports/sales", params={"store_id": store, "date_from": biz})
    ).json()
    assert Decimal(rep["service_charge_total"]) == Decimal("20")
    assert Decimal(rep["net_sales"]) == Decimal("220")  # 對帳: 報表 = 抽屜實收


async def test_service_charge_can_be_removed(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    sess = await _open_with_lines(client, store, "100", "1")
    await client.post(f"/tables/sessions/{sess}/service-charge", json={"rate": "0.1"})
    q = (
        await client.post(f"/tables/sessions/{sess}/service-charge", json={"rate": "0"})
    ).json()
    assert Decimal(q["net"]) == Decimal("100")


# ── 招待 (comp) ──────────────────────────────────────────────────────────────


async def test_comp_zeroes_the_bill(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    sess = await _open_with_lines(client, store, "150", "2")  # gross 300
    d = await client.post(
        f"/tables/sessions/{sess}/discount",
        json={"kind": "comp", "value": "0", "reason": "老闆招待"},
    )
    assert d.status_code == 200, d.text
    q = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert Decimal(q["net"]) == Decimal("0")
    # checkout with 0 cash settles the comped bill
    co = await client.post(f"/tables/sessions/{sess}/checkout", json={"tendered": "0"})
    assert co.status_code == 200, co.text
    assert Decimal(co.json()["total"]) == Decimal("0")


# ── 拆單/分開結帳 ────────────────────────────────────────────────────────────


async def test_split_payment_two_ways_closes_when_settled(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    sess = await _open_with_lines(client, store, "200", "2")  # due 400

    # 第一位客人付 150,給 200 → 找 50,帳單未結
    p1 = await client.post(
        f"/tables/sessions/{sess}/pay", json={"amount": "150", "tendered": "200"}
    )
    assert p1.status_code == 200, p1.text
    b1 = p1.json()
    assert Decimal(b1["change"]) == Decimal("50")
    assert Decimal(b1["remaining"]) == Decimal("250")
    assert b1["closed"] is False

    # quote 反映已收
    q = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert Decimal(q["paid"]) == Decimal("150")
    assert Decimal(q["remaining"]) == Decimal("250")

    # 第二位付清 250 → 結單結桌
    p2 = await client.post(
        f"/tables/sessions/{sess}/pay", json={"amount": "250", "tendered": "250"}
    )
    b2 = p2.json()
    assert b2["closed"] is True
    assert Decimal(b2["remaining"]) == Decimal("0")

    # 桌位已釋出 (the session is closed, so no occupied table carries it)
    fp = (await client.get("/tables/floor", params={"store_id": store})).json()
    assert all(
        not (x["session"] and x["session"]["id"] == sess) for x in fp
    )


async def test_partial_then_checkout_charges_only_remaining(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    sess = await _open_with_lines(client, store, "100", "3")  # due 300
    await client.post(
        f"/tables/sessions/{sess}/pay", json={"amount": "100", "tendered": "100"}
    )
    co = await client.post(f"/tables/sessions/{sess}/checkout", json={"tendered": "200"})
    assert co.status_code == 200, co.text
    assert Decimal(co.json()["total"]) == Decimal("200")  # 只收剩餘
    assert Decimal(co.json()["change"]) == Decimal("0")


async def test_overpay_partial_rejected_422(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    sess = await _open_with_lines(client, store, "100", "1")  # due 100
    r = await client.post(
        f"/tables/sessions/{sess}/pay", json={"amount": "150", "tendered": "150"}
    )
    assert r.status_code == 422
