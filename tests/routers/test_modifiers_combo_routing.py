"""P7.3 — 口味選項組/加價購 + 套餐組合 + 廚房自動分站."""

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

    # Real manager employee so 退菜 (void) can authorize against a valid actor.
    manager = Employee(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        full_name="品項經理",
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


async def _open_session(client: httpx.AsyncClient, store: str) -> dict:
    t = (
        await client.post("/tables", json={"store_id": store, "name": f"M{uuid.uuid4().hex[:4]}"})
    ).json()
    return (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()


async def _menu_item(
    client: httpx.AsyncClient,
    store: str,
    price: str,
    *,
    station: str | None = None,
) -> dict:
    body = {
        "store_id": store,
        "sku": f"MI{uuid.uuid4().hex[:6]}",
        "name": "測試品項",
        "price": price,
    }
    if station is not None:
        body["default_kitchen_station"] = station
    r = await client.post("/menu/items", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ─── 口味選項組/加價購 ──────────────────────────────────────────────────────


async def test_modifier_price_delta_folds_into_line_total(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    item = await _menu_item(client, store, "50")
    g = await client.post(
        f"/menu/items/{item['id']}/modifier-groups",
        json={
            "name": "加料",
            "min_select": 0,
            "max_select": 2,
            "options": [
                {"name": "加蛋", "price_delta": "10"},
                {"name": "加起司", "price_delta": "15"},
            ],
        },
    )
    assert g.status_code == 201, g.text
    group = g.json()
    egg = next(o for o in group["options"] if o["name"] == "加蛋")
    cheese = next(o for o in group["options"] if o["name"] == "加起司")

    sess = await _open_session(client, store)
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={
            "menu_item_id": item["id"],
            "qty": "2",
            "modifier_option_ids": [egg["id"], cheese["id"]],
        },
    )
    assert r.status_code == 201, r.text
    line = next(ln for ln in r.json()["lines"] if ln["menu_item_id"] == item["id"])
    # unit_price = 50 + 10 + 15 = 75; qty 2 → 150
    assert Decimal(line["unit_price"]) == Decimal("75")
    assert Decimal(line["line_total"]) == Decimal("150")
    names = {m["option_name"] for m in line["modifiers"]}
    assert names == {"加起司", "加蛋"}


async def test_required_modifier_group_enforced(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    item = await _menu_item(client, store, "60")
    g = await client.post(
        f"/menu/items/{item['id']}/modifier-groups",
        json={
            "name": "甜度",
            "min_select": 1,
            "max_select": 1,
            "options": [{"name": "正常糖"}, {"name": "半糖"}],
        },
    )
    assert g.status_code == 201, g.text

    sess = await _open_session(client, store)
    # no modifier selected → required group unmet
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": item["id"], "qty": "1"},
    )
    assert r.status_code == 422, r.text

    option = next(o for o in g.json()["options"] if o["name"] == "半糖")
    r2 = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": item["id"], "qty": "1", "modifier_option_ids": [option["id"]]},
    )
    assert r2.status_code == 201, r2.text


async def test_over_selecting_max_select_rejected(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    item = await _menu_item(client, store, "60")
    g = await client.post(
        f"/menu/items/{item['id']}/modifier-groups",
        json={
            "name": "甜度",
            "min_select": 1,
            "max_select": 1,
            "options": [{"name": "正常糖"}, {"name": "半糖"}],
        },
    )
    opts = g.json()["options"]
    sess = await _open_session(client, store)
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={
            "menu_item_id": item["id"],
            "qty": "1",
            "modifier_option_ids": [o["id"] for o in opts],
        },
    )
    assert r.status_code == 422, r.text


# ─── 廚房自動分站 ───────────────────────────────────────────────────────────


async def test_line_auto_routes_to_item_default_station(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    drink = await _menu_item(client, store, "40", station="bar")
    sess = await _open_session(client, store)
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": drink["id"], "qty": "1"}
    )
    assert r.status_code == 201, r.text
    line = r.json()["lines"][0]
    assert line["kitchen_station"] == "bar"


async def test_explicit_station_overrides_item_default(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    drink = await _menu_item(client, store, "40", station="bar")
    sess = await _open_session(client, store)
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": drink["id"], "qty": "1", "kitchen_station": "counter"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["lines"][0]["kitchen_station"] == "counter"


async def test_no_default_station_falls_back_to_kitchen(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    item = await _menu_item(client, store, "60")
    sess = await _open_session(client, store)
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": item["id"], "qty": "1"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["lines"][0]["kitchen_station"] == "kitchen"


# ─── 套餐組合 ────────────────────────────────────────────────────────────────


async def test_combo_expands_into_zero_price_component_lines(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    burger = await _menu_item(client, store, "0", station="kitchen")
    fries = await _menu_item(client, store, "0", station="kitchen")
    cola = await _menu_item(client, store, "0", station="bar")
    combo = await _menu_item(client, store, "199")

    for comp in (burger, fries, cola):
        r = await client.post(
            f"/menu/items/{combo['id']}/combo-components",
            json={"component_item_id": comp["id"], "qty": "1"},
        )
        assert r.status_code == 201, r.text

    sess = await _open_session(client, store)
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": combo["id"], "qty": "1"}
    )
    assert r.status_code == 201, r.text
    lines = r.json()["lines"]
    assert len(lines) == 4  # combo header + 3 components

    header = next(ln for ln in lines if ln["menu_item_id"] == combo["id"])
    assert header["combo_parent_id"] is None
    assert Decimal(header["unit_price"]) == Decimal("199")

    children = [ln for ln in lines if ln["combo_parent_id"] == header["id"]]
    assert len(children) == 3
    assert all(Decimal(c["unit_price"]) == Decimal("0") for c in children)
    cola_line = next(c for c in children if c["menu_item_id"] == cola["id"])
    assert cola_line["kitchen_station"] == "bar"

    # total due is just the combo price — components don't double-charge.
    q = (await client.get(f"/tables/sessions/{sess['id']}/quote")).json()
    assert Decimal(q["net"]) == Decimal("199")


async def test_voiding_combo_header_voids_its_components_too(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    side = await _menu_item(client, store, "0")
    combo = await _menu_item(client, store, "150")
    r = await client.post(
        f"/menu/items/{combo['id']}/combo-components",
        json={"component_item_id": side["id"], "qty": "1"},
    )
    assert r.status_code == 201, r.text

    sess = await _open_session(client, store)
    add = await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": combo["id"], "qty": "1"}
    )
    lines = add.json()["lines"]
    assert len(lines) == 2
    header = next(ln for ln in lines if ln["menu_item_id"] == combo["id"])

    v = await client.post(f"/tables/order-lines/{header['id']}/void", json={"reason": "客訴"})
    assert v.status_code == 200, v.text
    assert v.json()["lines"] == []
