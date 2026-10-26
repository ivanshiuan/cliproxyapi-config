"""P7.5 — 候位先點餐 (pre-order while waiting, seat carries it to the table)."""

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


async def _item(client: httpx.AsyncClient, store: str, price: str) -> dict:
    r = await client.post(
        "/menu/items",
        json={"store_id": store, "sku": f"QP{uuid.uuid4().hex[:6]}", "name": "候位品項", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _join_queue(client: httpx.AsyncClient, store: str, party_size: int = 2) -> dict:
    r = await client.post(
        "/queue",
        json={"store_id": store, "contact_name": "候位客", "contact_phone": "0900000000", "party_size": party_size},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_preorder_line_creates_order_bound_to_queue_entry(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "70")
    entry = await _join_queue(client, store)

    r = await client.post(f"/queue/{entry['id']}/lines", json={"menu_item_id": it["id"], "qty": "2"})
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "open"
    assert order["table_session_id"] is None
    assert Decimal(order["lines"][0]["line_total"]) == Decimal("140")


async def test_seat_reparents_preorder_onto_new_session(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "90")
    entry = await _join_queue(client, store)
    pre = (
        await client.post(f"/queue/{entry['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"})
    ).json()

    t = (await client.post("/tables", json={"store_id": store, "name": f"QP-{uuid.uuid4().hex[:4]}"})).json()
    seat = await client.post(f"/queue/{entry['id']}/seat", json={"table_id": t["id"]})
    assert seat.status_code == 200, seat.text
    body = seat.json()
    assert body["queue_entry"]["status"] == "seated"
    session_id = body["table_session"]["id"]

    order = (await client.get(f"/tables/sessions/{session_id}/order")).json()
    assert order["id"] == pre["id"]
    assert len(order["lines"]) == 1
    assert Decimal(order["lines"][0]["line_total"]) == Decimal("90")

    # floor board shows the table occupied now
    floor = (await client.get("/tables/floor", params={"store_id": store})).json()
    row = next(x for x in floor if x["id"] == t["id"])
    assert row["is_occupied"] is True


async def test_seat_without_preorder_still_works(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    entry = await _join_queue(client, store)
    t = (await client.post("/tables", json={"store_id": store, "name": f"QP-{uuid.uuid4().hex[:4]}"})).json()
    seat = await client.post(f"/queue/{entry['id']}/seat", json={"table_id": t["id"]})
    assert seat.status_code == 200, seat.text
    assert seat.json()["queue_entry"]["status"] == "seated"


async def test_preorder_after_seated_rejected(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    it = await _item(client, store, "50")
    entry = await _join_queue(client, store)
    t = (await client.post("/tables", json={"store_id": store, "name": f"QP-{uuid.uuid4().hex[:4]}"})).json()
    await client.post(f"/queue/{entry['id']}/seat", json={"table_id": t["id"]})

    r = await client.post(f"/queue/{entry['id']}/lines", json={"menu_item_id": it["id"], "qty": "1"})
    assert r.status_code == 409, r.text


async def test_seat_twice_rejected(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    entry = await _join_queue(client, store)
    t1 = (await client.post("/tables", json={"store_id": store, "name": f"QP-{uuid.uuid4().hex[:4]}"})).json()
    t2 = (await client.post("/tables", json={"store_id": store, "name": f"QP-{uuid.uuid4().hex[:4]}"})).json()
    r1 = await client.post(f"/queue/{entry['id']}/seat", json={"table_id": t1["id"]})
    assert r1.status_code == 200
    r2 = await client.post(f"/queue/{entry['id']}/seat", json={"table_id": t2["id"]})
    assert r2.status_code == 409, r2.text
