"""P3 KDS flow tests — POS/QR lines land on the kitchen board, cards carry the
joined display names, and every status transition hits the real-time stream.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

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


async def _seat_and_order(client: httpx.AsyncClient, store: str) -> tuple[dict, dict]:
    """Open a table and add one POS line (no explicit station). Return (table, order)."""
    t = (
        await client.post(
            "/tables", json={"store_id": store, "name": f"K{uuid.uuid4().hex[:4]}"}
        )
    ).json()
    it = (
        await client.post(
            "/menu/items",
            json={
                "store_id": store,
                "sku": f"K{uuid.uuid4().hex[:5]}",
                "name": "炒飯",
                "price": "120",
            },
        )
    ).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    order = (
        await client.post(
            f"/tables/sessions/{sess['id']}/lines",
            json={"menu_item_id": it["id"], "qty": "1", "notes": "不要蔥"},
        )
    ).json()
    return t, order


async def test_pos_line_defaults_onto_kds_with_names(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t, order = await _seat_and_order(client, store)

    q = await client.get("/kitchen/queue", params={"store_id": store, "kitchen_status": "queued"})
    assert q.status_code == 200, q.text
    mine = [r for r in q.json() if r["order_id"] == order["id"]]
    assert len(mine) == 1, "POS line must land on the KDS without explicit station"
    row = mine[0]
    assert row["kitchen_station"] == "kitchen"
    assert row["item_name"] == "炒飯"
    assert row["table_name"] == t["name"]
    assert row["notes"] == "不要蔥"


async def test_kitchen_transition_emits_floor_event(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    _t, order = await _seat_and_order(client, store)
    line_id = order["lines"][0]["id"]

    r = await client.patch(f"/kitchen/lines/{line_id}/status", json={"status": "cooking"})
    assert r.status_code == 200, r.text

    evs = (
        await client.get("/tables/events", params={"store_id": store, "after": 0})
    ).json()
    kitchen_evs = [e for e in evs if e["type"] == "kitchen.status_changed"]
    assert kitchen_evs, "status change must land on the live floor stream"
    assert kitchen_evs[-1]["payload"]["status"] == "cooking"
    assert kitchen_evs[-1]["payload"]["line_id"] == line_id


async def test_full_kds_lifecycle_from_pos_order(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    _t, order = await _seat_and_order(client, store)
    line_id = order["lines"][0]["id"]

    for status in ("cooking", "ready", "served"):
        r = await client.patch(f"/kitchen/lines/{line_id}/status", json={"status": status})
        assert r.status_code == 200, (status, r.text)
    # Served lines stay queryable but are off the active lanes.
    served = await client.get(
        "/kitchen/queue", params={"store_id": store, "kitchen_status": "served"}
    )
    assert any(x["line_id"] == line_id for x in served.json())
