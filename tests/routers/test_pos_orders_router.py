"""Integration tests for POS session ordering + cash checkout (P1.3b / P1.4).

Exercises the full till flow through real HTTP: open table → add lines →
change qty → 退菜 → cash checkout with change → session auto-closed.
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

    # A real manager employee so gated ops (退菜) audit against an existing
    # actor_id (audit_log.actor_id FKs employees). Auth-specific deny/override
    # paths are covered in test_pos_auth_router.
    manager = Employee(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        full_name="測試經理",
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


async def _table(client: httpx.AsyncClient, store_id: str, name: str = "A1") -> dict:
    r = await client.post("/tables", json={"store_id": store_id, "name": name, "seats": 4})
    assert r.status_code == 201, r.text
    return r.json()


async def _item(client: httpx.AsyncClient, store_id: str, sku: str, price: str) -> dict:
    r = await client.post(
        "/menu/items",
        json={"store_id": store_id, "sku": sku, "name": f"品項{sku}", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _open(client: httpx.AsyncClient, table_id: str) -> dict:
    r = await client.post(f"/tables/{table_id}/open", json={"party_size": 2})
    assert r.status_code == 201, r.text
    return r.json()


async def test_add_lines_and_order_bound_to_session(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store)
    it = await _item(client, store, "X1", "120")
    sess = await _open(client, t["id"])

    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it["id"], "qty": "2"},
    )
    assert r.status_code == 201, r.text
    order = r.json()
    assert len(order["lines"]) == 1
    assert Decimal(order["lines"][0]["line_total"]) == Decimal("240")

    # second add re-uses the same open order
    it2 = await _item(client, store, "X2", "50")
    r2 = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it2["id"], "qty": "1"},
    )
    assert r2.status_code == 201
    assert len(r2.json()["lines"]) == 2
    assert r2.json()["id"] == order["id"]  # same order


async def test_price_snapshot_ignores_client_and_uses_menu(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store)
    it = await _item(client, store, "P1", "88")
    sess = await _open(client, t["id"])

    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it["id"], "qty": "1", "unit_price": "1"},  # bogus price ignored
    )
    assert r.status_code == 201
    assert Decimal(r.json()["lines"][0]["unit_price"]) == Decimal("88")


async def test_update_qty_and_void_line(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store)
    it = await _item(client, store, "U1", "100")
    sess = await _open(client, t["id"])

    order = (
        await client.post(
            f"/tables/sessions/{sess['id']}/lines",
            json={"menu_item_id": it["id"], "qty": "1"},
        )
    ).json()
    line_id = order["lines"][0]["id"]

    upd = await client.patch(f"/tables/order-lines/{line_id}", json={"qty": "3"})
    assert upd.status_code == 200
    assert Decimal(upd.json()["lines"][0]["line_total"]) == Decimal("300")

    void = await client.post(
        f"/tables/order-lines/{line_id}/void", json={"reason": "客人不要了"}
    )
    assert void.status_code == 200
    assert void.json()["lines"] == []


async def test_cash_checkout_computes_change_and_closes_session(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store)
    it = await _item(client, store, "C1", "150")
    sess = await _open(client, t["id"])

    await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it["id"], "qty": "2"},  # total 300
    )

    co = await client.post(
        f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "500"}
    )
    assert co.status_code == 200, co.text
    body = co.json()
    assert Decimal(body["total"]) == Decimal("300")
    assert Decimal(body["change"]) == Decimal("200")
    assert body["order"]["status"] == "closed"

    # table is free again on the floor
    fp = await client.get("/tables/floor", params={"store_id": store})
    view = next(x for x in fp.json() if x["id"] == t["id"])
    assert view["is_occupied"] is False


async def test_checkout_insufficient_cash_422(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store)
    it = await _item(client, store, "I1", "200")
    sess = await _open(client, t["id"])
    await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it["id"], "qty": "1"},
    )
    co = await client.post(
        f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "100"}
    )
    assert co.status_code == 422


async def test_checkout_empty_order_422(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store)
    sess = await _open(client, t["id"])
    # ensure an (empty) order exists
    await client.get(f"/tables/sessions/{sess['id']}/order")
    co = await client.post(
        f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "100"}
    )
    assert co.status_code == 422


async def test_cannot_add_line_to_closed_session(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store)
    it = await _item(client, store, "Z1", "50")
    sess = await _open(client, t["id"])
    await client.post(f"/tables/sessions/{sess['id']}/close")

    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it["id"], "qty": "1"},
    )
    assert r.status_code == 409
