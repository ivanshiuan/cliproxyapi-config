"""Integration tests for the /tables router (POS floor plan + sessions, P1.3)."""

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


async def _mk_table(client: httpx.AsyncClient, store_id: str, name: str, **kw) -> dict:
    resp = await client.post(
        "/tables", json={"store_id": store_id, "name": name, "seats": 4, **kw}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_table_gets_qr_token_and_lists(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t = await _mk_table(client, str(seed_store.id), "A1")
    assert t["qr_token"]  # auto-generated
    assert t["name"] == "A1"

    listing = await client.get("/tables", params={"store_id": str(seed_store.id)})
    assert "A1" in [x["name"] for x in listing.json()]


async def test_duplicate_table_name_conflicts(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    await _mk_table(client, str(seed_store.id), "A1")
    dup = await client.post(
        "/tables", json={"store_id": str(seed_store.id), "name": "A1", "seats": 2}
    )
    assert dup.status_code == 409


async def test_open_close_cycle_and_floor_plan(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t = await _mk_table(client, str(seed_store.id), "A1")

    # floor plan: table free
    fp = await client.get("/tables/floor", params={"store_id": str(seed_store.id)})
    assert fp.status_code == 200
    view = next(x for x in fp.json() if x["id"] == t["id"])
    assert view["is_occupied"] is False
    assert view["session"] is None

    # open a session
    opened = await client.post(f"/tables/{t['id']}/open", json={"party_size": 3})
    assert opened.status_code == 201, opened.text
    sess = opened.json()
    assert sess["status"] == "open"
    assert sess["party_size"] == 3

    # floor plan now occupied
    fp2 = await client.get("/tables/floor", params={"store_id": str(seed_store.id)})
    view2 = next(x for x in fp2.json() if x["id"] == t["id"])
    assert view2["is_occupied"] is True
    assert view2["session"]["id"] == sess["id"]

    # close it
    closed = await client.post(f"/tables/sessions/{sess['id']}/close")
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    # table free again
    fp3 = await client.get("/tables/floor", params={"store_id": str(seed_store.id)})
    view3 = next(x for x in fp3.json() if x["id"] == t["id"])
    assert view3["is_occupied"] is False


async def test_double_open_same_table_conflicts(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t = await _mk_table(client, str(seed_store.id), "A1")
    first = await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})
    assert first.status_code == 201
    second = await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})
    assert second.status_code == 409


async def test_reopen_after_close_succeeds(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t = await _mk_table(client, str(seed_store.id), "A1")
    s1 = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/sessions/{s1['id']}/close")
    s2 = await client.post(f"/tables/{t['id']}/open", json={"party_size": 4})
    assert s2.status_code == 201


async def test_transfer_session_to_free_table(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    a = await _mk_table(client, str(seed_store.id), "A1")
    b = await _mk_table(client, str(seed_store.id), "B1")
    sess = (await client.post(f"/tables/{a['id']}/open", json={"party_size": 2})).json()

    moved = await client.post(
        f"/tables/sessions/{sess['id']}/transfer", json={"target_table_id": b["id"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["table_id"] == b["id"]

    # A is now free, B occupied
    fp = await client.get("/tables/floor", params={"store_id": str(seed_store.id)})
    views = {x["id"]: x for x in fp.json()}
    assert views[a["id"]]["is_occupied"] is False
    assert views[b["id"]]["is_occupied"] is True


async def test_transfer_to_occupied_table_conflicts(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    a = await _mk_table(client, str(seed_store.id), "A1")
    b = await _mk_table(client, str(seed_store.id), "B1")
    sess_a = (await client.post(f"/tables/{a['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/{b['id']}/open", json={"party_size": 2})

    moved = await client.post(
        f"/tables/sessions/{sess_a['id']}/transfer", json={"target_table_id": b["id"]}
    )
    assert moved.status_code == 409


async def test_delete_table_with_open_session_conflicts(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t = await _mk_table(client, str(seed_store.id), "A1")
    await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})
    dele = await client.delete(f"/tables/{t['id']}")
    assert dele.status_code == 409


async def test_close_already_closed_session_conflicts(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    t = await _mk_table(client, str(seed_store.id), "A1")
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(f"/tables/sessions/{sess['id']}/close")
    again = await client.post(f"/tables/sessions/{sess['id']}/close")
    assert again.status_code == 409
