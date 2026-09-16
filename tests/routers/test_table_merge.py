"""併桌 (table merge) tests — fold one open session's bill into another."""

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


async def _seat_with_line(
    client: httpx.AsyncClient, store: str, price: str, qty: str
) -> tuple[dict, dict]:
    t = (
        await client.post(
            "/tables", json={"store_id": store, "name": f"M{uuid.uuid4().hex[:4]}"}
        )
    ).json()
    it = (
        await client.post(
            "/menu/items",
            json={"store_id": store, "sku": f"M{uuid.uuid4().hex[:5]}", "name": "品", "price": price},
        )
    ).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": it["id"], "qty": qty},
    )
    assert r.status_code == 201, r.text
    return t, sess


async def test_merge_moves_lines_and_closes_source(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t1, sess1 = await _seat_with_line(client, store, "100", "2")  # 200
    t2, sess2 = await _seat_with_line(client, store, "50", "3")  # 150

    r = await client.post(
        f"/tables/sessions/{sess1['id']}/merge",
        json={"source_session_id": sess2["id"], "reason": "客人併桌"},
    )
    assert r.status_code == 200, r.text
    merged = r.json()
    assert len(merged["lines"]) == 2
    total = sum(Decimal(ln["line_total"]) for ln in merged["lines"])
    assert total == Decimal("350")

    # target quote reflects the combined bill
    q = (await client.get(f"/tables/sessions/{sess1['id']}/quote")).json()
    assert Decimal(q["net"]) == Decimal("350")

    # source table freed on the floor board; target still occupied
    fp = (await client.get("/tables/floor", params={"store_id": store})).json()
    v1 = next(x for x in fp if x["id"] == t1["id"])
    v2 = next(x for x in fp if x["id"] == t2["id"])
    assert v1["is_occupied"] is True
    assert v2["is_occupied"] is False

    # source session is no longer open (cannot add lines to it)
    r2 = await client.get(f"/tables/sessions/{sess2['id']}/order")
    assert r2.status_code == 409

    # checkout the merged bill for the full combined amount
    co = await client.post(
        f"/tables/sessions/{sess1['id']}/checkout", json={"tendered": "350"}
    )
    assert co.status_code == 200, co.text
    assert Decimal(co.json()["total"]) == Decimal("350")


async def test_merge_into_self_rejected_409(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    _t, sess = await _seat_with_line(client, store, "80", "1")
    r = await client.post(
        f"/tables/sessions/{sess['id']}/merge",
        json={"source_session_id": sess["id"]},
    )
    assert r.status_code == 409


async def test_merge_with_source_already_partially_paid_rejected(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    _t1, sess1 = await _seat_with_line(client, store, "100", "1")
    _t2, sess2 = await _seat_with_line(client, store, "100", "2")  # due 200
    await client.post(
        f"/tables/sessions/{sess2['id']}/pay", json={"amount": "50", "tendered": "50"}
    )
    r = await client.post(
        f"/tables/sessions/{sess1['id']}/merge",
        json={"source_session_id": sess2["id"]},
    )
    assert r.status_code == 409


async def test_merge_empty_source_still_frees_its_table(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    _t1, sess1 = await _seat_with_line(client, store, "100", "1")
    t2 = (
        await client.post("/tables", json={"store_id": store, "name": f"E{uuid.uuid4().hex[:4]}"})
    ).json()
    sess2 = (await client.post(f"/tables/{t2['id']}/open", json={"party_size": 1})).json()

    r = await client.post(
        f"/tables/sessions/{sess1['id']}/merge",
        json={"source_session_id": sess2["id"]},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["lines"]) == 1  # only target's original line

    fp = (await client.get("/tables/floor", params={"store_id": store})).json()
    v2 = next(x for x in fp if x["id"] == t2["id"])
    assert v2["is_occupied"] is False
