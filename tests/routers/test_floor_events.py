"""Integration tests for the real-time floor event stream (P1.5).

The WebSocket wire protocol is verified live (L3, python websockets client vs a
real uvicorn); here we pin the *durable* layer that guarantees correctness — the
append-only ``order_events`` log and the ``seq`` cursor that a reconnecting
tablet replays from. Every floor mutation must leave exactly the right event
behind, in order, and the log must be immutable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
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


async def _table(client: httpx.AsyncClient, store_id: str, name: str = "E1") -> dict:
    r = await client.post("/tables", json={"store_id": store_id, "name": name, "seats": 4})
    assert r.status_code == 201, r.text
    return r.json()


async def _events(client: httpx.AsyncClient, store_id: str, after: int = 0) -> list[dict]:
    r = await client.get("/tables/events", params={"store_id": store_id, "after": after})
    assert r.status_code == 200, r.text
    return r.json()


async def test_open_and_close_emit_ordered_events(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store)

    opened = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 3})).json()
    evs = await _events(client, store)
    types = [e["type"] for e in evs]
    assert "session.opened" in types
    ev_open = next(e for e in evs if e["type"] == "session.opened")
    assert ev_open["table_id"] == t["id"]
    assert ev_open["session_id"] == opened["id"]

    await client.post(f"/tables/sessions/{opened['id']}/close")
    evs2 = await _events(client, store)
    types2 = [e["type"] for e in evs2]
    assert types2[-1] == "session.closed"
    # seq is strictly monotonic across the two mutations.
    seqs = [e["seq"] for e in evs2]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


async def test_cursor_after_returns_only_newer_events(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store, name="E2")
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 1})).json()

    first = await _events(client, store)
    assert first, "opening a table must emit at least one event"
    cursor = max(e["seq"] for e in first)

    # A change after the cursor shows up; nothing at/below the cursor repeats.
    await client.post(f"/tables/sessions/{sess['id']}/cancel")
    delta = await _events(client, store, after=cursor)
    assert delta, "the cancel must appear after the cursor"
    assert all(e["seq"] > cursor for e in delta)
    assert delta[-1]["type"] == "session.cancelled"


async def test_full_till_loop_event_trail(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = await _table(client, store, name="E3")
    item = (
        await client.post(
            "/menu/items",
            json={"store_id": store, "sku": "EV1", "name": "測試品", "price": "100"},
        )
    ).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    await client.post(
        f"/tables/sessions/{sess['id']}/lines",
        json={"menu_item_id": item["id"], "qty": "2"},
    )
    co = await client.post(f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "500"})
    assert co.status_code == 200, co.text

    trail = [e["type"] for e in await _events(client, store)]
    # The whole loop is reconstructable from the stream, in order.
    assert trail.index("session.opened") < trail.index("order.line_added")
    assert trail.index("order.line_added") < trail.index("order.checkout")


async def test_order_events_log_is_append_only(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_store: Store
) -> None:
    """DB-level INSTEAD NOTHING rules must drop UPDATE/DELETE on the stream."""
    store = str(seed_store.id)
    t = await _table(client, store, name="E4")
    await client.post(f"/tables/{t['id']}/open", json={"party_size": 1})

    row = (
        await db_session.execute(
            text(
                "SELECT id, event_type FROM order_events "
                "WHERE store_id = :s ORDER BY seq DESC LIMIT 1"
            ),
            {"s": store},
        )
    ).first()
    assert row is not None
    ev_id, original_type = row

    # UPDATE is silently dropped (ROW_COUNT 0) — value unchanged.
    await db_session.execute(
        text("UPDATE order_events SET event_type = 'tampered' WHERE id = :i"),
        {"i": ev_id},
    )
    still = (
        await db_session.execute(
            text("SELECT event_type FROM order_events WHERE id = :i"), {"i": ev_id}
        )
    ).scalar_one()
    assert still == original_type

    # DELETE is silently dropped — row survives.
    await db_session.execute(text("DELETE FROM order_events WHERE id = :i"), {"i": ev_id})
    survived = (
        await db_session.execute(
            text("SELECT count(*) FROM order_events WHERE id = :i"), {"i": ev_id}
        )
    ).scalar_one()
    assert survived == 1
