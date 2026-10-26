"""交班/關帳 (cash drawer open/close, P7.2) tests."""

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

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _employee(db: AsyncSession, tenant: Tenant, store: Store, name: str) -> Employee:
    emp = Employee(
        tenant_id=tenant.id,
        store_id=store.id,
        full_name=name,
        role=EmployeeRole.STAFF,
        hourly_wage=Decimal("183"),
        hired_on=date(2026, 1, 1),
    )
    db.add(emp)
    await db.flush()
    return emp


async def test_open_close_no_sales_matches_float(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    store = str(seed_store.id)
    emp = await _employee(db_session, seed_tenant, seed_store, "開班員")

    o = await client.post(
        "/cash-drawer/open",
        json={"store_id": store, "opened_by": str(emp.id), "opening_float": "5000"},
    )
    assert o.status_code == 200, o.text
    drawer = o.json()

    cur = await client.get("/cash-drawer/current", params={"store_id": store})
    assert cur.status_code == 200
    assert cur.json()["id"] == drawer["id"]

    exp = await client.get(f"/cash-drawer/{drawer['id']}/expected")
    assert exp.status_code == 200
    assert Decimal(exp.json()["expected_close"]) == Decimal("5000")

    c = await client.post(
        f"/cash-drawer/{drawer['id']}/close",
        json={"closed_by": str(emp.id), "closing_actual": "5000"},
    )
    assert c.status_code == 200, c.text
    body = c.json()
    assert Decimal(body["variance"]) == Decimal("0")
    assert body["session"]["closed_at"] is not None

    # register free again
    cur2 = await client.get("/cash-drawer/current", params={"store_id": store})
    assert cur2.json() is None


async def test_close_reflects_real_cash_sales_and_variance(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    store = str(seed_store.id)
    emp = await _employee(db_session, seed_tenant, seed_store, "夜班員")
    drawer = (
        await client.post(
            "/cash-drawer/open",
            json={"store_id": store, "opened_by": str(emp.id), "opening_float": "3000"},
        )
    ).json()

    it = (
        await client.post(
            "/menu/items",
            json={"store_id": store, "sku": f"CD{uuid.uuid4().hex[:5]}", "name": "品", "price": "200"},
        )
    ).json()
    t = (await client.post("/tables", json={"store_id": store, "name": f"CD{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"}
    )
    co = await client.post(f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "200"})
    assert co.status_code == 200, co.text

    exp = (await client.get(f"/cash-drawer/{drawer['id']}/expected")).json()
    assert Decimal(exp["cash_sales"]) == Decimal("200")
    assert Decimal(exp["expected_close"]) == Decimal("3200")

    # 少收 5 元 → variance -5
    c = await client.post(
        f"/cash-drawer/{drawer['id']}/close",
        json={"closed_by": str(emp.id), "closing_actual": "3195"},
    )
    body = c.json()
    assert Decimal(body["variance"]) == Decimal("-5")
    assert Decimal(body["net_sales"]) == Decimal("200")
    assert body["order_count"] == 1


async def test_cannot_open_twice_for_same_register(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    store = str(seed_store.id)
    emp = await _employee(db_session, seed_tenant, seed_store, "員工A")
    r1 = await client.post(
        "/cash-drawer/open",
        json={"store_id": store, "opened_by": str(emp.id), "opening_float": "5000"},
    )
    assert r1.status_code == 200
    r2 = await client.post(
        "/cash-drawer/open",
        json={"store_id": store, "opened_by": str(emp.id), "opening_float": "5000"},
    )
    assert r2.status_code == 409


async def test_cannot_close_twice(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    store = str(seed_store.id)
    emp = await _employee(db_session, seed_tenant, seed_store, "員工B")
    drawer = (
        await client.post(
            "/cash-drawer/open",
            json={"store_id": store, "opened_by": str(emp.id), "opening_float": "5000"},
        )
    ).json()
    c1 = await client.post(
        f"/cash-drawer/{drawer['id']}/close",
        json={"closed_by": str(emp.id), "closing_actual": "5000"},
    )
    assert c1.status_code == 200
    c2 = await client.post(
        f"/cash-drawer/{drawer['id']}/close",
        json={"closed_by": str(emp.id), "closing_actual": "5000"},
    )
    assert c2.status_code == 409
