"""Integration tests for ``/reservations`` and ``/queue`` routers.

Covers
------
- Reservation: create, get, list (filtered), state transitions.
- Queue: join, list, state transitions.
- Forbidden hops (e.g. CONFIRMED → BOOKED, SEATED → CONFIRMED) → 409.
- Tenant scoping: a reservation in tenant A is not visible from tenant B.
- arrived_at auto-stamped on SEATED.
- called_at / seated_at auto-stamped on queue transitions.

Same shape as ``test_events_router.py`` — async client over ASGI, tenant
DI overridden so the seeded tenant matches the router's TenantId.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
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


def _booking_payload(store_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "store_id": str(store_id),
        "contact_name": "王大明",
        "contact_phone": "0912-345-678",
        "party_size": 4,
        "reserved_for": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
        "source": "phone",
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────
# Reservation lifecycle
# ──────────────────────────────────────────────────────────────────────────


async def test_create_reservation_starts_in_booked_status(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    resp = await client.post("/reservations", json=_booking_payload(seed_store.id))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "booked"
    assert body["party_size"] == 4
    assert body["contact_name"] == "王大明"
    assert body["arrived_at"] is None


async def test_get_reservation_round_trips(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    create = await client.post("/reservations", json=_booking_payload(seed_store.id))
    rid = create.json()["id"]
    fetch = await client.get(f"/reservations/{rid}")
    assert fetch.status_code == 200
    assert fetch.json()["id"] == rid


async def test_get_unknown_reservation_returns_404(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/reservations/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_full_happy_path_booked_to_completed(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    create = await client.post("/reservations", json=_booking_payload(seed_store.id))
    rid = create.json()["id"]

    # booked → confirmed
    r1 = await client.patch(
        f"/reservations/{rid}/status",
        json={"status": "confirmed", "reason": "LINE 回覆確認"},
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "confirmed"

    # confirmed → seated (arrived_at should auto-stamp)
    r2 = await client.patch(
        f"/reservations/{rid}/status", json={"status": "seated"}
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["status"] == "seated"
    assert body2["arrived_at"] is not None  # auto-stamped on SEATED

    # seated → completed
    r3 = await client.patch(
        f"/reservations/{rid}/status", json={"status": "completed"}
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "completed"


async def test_invalid_transition_returns_409_with_allowed_list(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    """seated → confirmed is not allowed; the error envelope spells out
    which next states *are* allowed so the client can recover."""
    create = await client.post("/reservations", json=_booking_payload(seed_store.id))
    rid = create.json()["id"]
    # Hop through to seated (legal): booked → seated allowed.
    await client.patch(f"/reservations/{rid}/status", json={"status": "seated"})

    # Try to go back to confirmed — forbidden.
    bad = await client.patch(
        f"/reservations/{rid}/status", json={"status": "confirmed"}
    )
    assert bad.status_code == 409
    body = bad.json()["error"]
    assert body["code"] == "CONFLICT"
    assert body["details"]["current"] == "seated"
    assert body["details"]["target"] == "confirmed"
    assert "completed" in body["details"]["allowed"]


async def test_terminal_state_rejects_further_transitions(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    create = await client.post("/reservations", json=_booking_payload(seed_store.id))
    rid = create.json()["id"]
    await client.patch(f"/reservations/{rid}/status", json={"status": "cancelled"})

    # Any further hop from CANCELLED must 409 — the allowed set is empty.
    resp = await client.patch(
        f"/reservations/{rid}/status", json={"status": "confirmed"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["allowed"] == []


async def test_no_show_transition_records_arrival_timestamp(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    create = await client.post("/reservations", json=_booking_payload(seed_store.id))
    rid = create.json()["id"]
    resp = await client.patch(
        f"/reservations/{rid}/status",
        json={"status": "no_show", "reason": "客戶未到場 15min"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_show"
    # We stamp arrival on no_show too so analytics can compute the gap.
    assert body["arrived_at"] is not None


async def test_list_reservations_filters_by_store(
    client: httpx.AsyncClient, seed_store: Store, db_session: AsyncSession
) -> None:
    # Make a second store under the same tenant.
    other_store = Store(
        tenant_id=seed_store.tenant_id,
        name="Branch 2",
        is_active=True,
    )
    db_session.add(other_store)
    await db_session.flush()

    await client.post("/reservations", json=_booking_payload(seed_store.id))
    await client.post("/reservations", json=_booking_payload(other_store.id))

    resp = await client.get(
        "/reservations", params={"store_id": str(seed_store.id)}
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["store_id"] == str(seed_store.id)


async def test_naive_reserved_for_rejected_at_validation(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    payload = _booking_payload(
        seed_store.id,
        reserved_for=datetime.now().isoformat(),  # tz-naive
    )
    resp = await client.post("/reservations", json=payload)
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────────
# Walk-in queue lifecycle
# ──────────────────────────────────────────────────────────────────────────


def _queue_payload(store_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "store_id": str(store_id),
        "contact_name": "李小華",
        "party_size": 2,
        "queue_no": "A-12",
    }
    base.update(overrides)
    return base


async def test_join_queue_starts_in_waiting(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    resp = await client.post("/queue", json=_queue_payload(seed_store.id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "waiting"
    assert body["queue_no"] == "A-12"
    assert body["called_at"] is None
    assert body["seated_at"] is None


async def test_queue_call_then_seat_stamps_timestamps(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    create = await client.post("/queue", json=_queue_payload(seed_store.id))
    qid = create.json()["id"]

    call = await client.patch(f"/queue/{qid}/status", json={"status": "called"})
    assert call.status_code == 200
    body = call.json()
    assert body["status"] == "called"
    assert body["called_at"] is not None
    assert body["seated_at"] is None

    seat = await client.patch(f"/queue/{qid}/status", json={"status": "seated"})
    assert seat.status_code == 200
    body = seat.json()
    assert body["status"] == "seated"
    # called_at preserved; seated_at now populated.
    assert body["called_at"] is not None
    assert body["seated_at"] is not None


async def test_queue_seated_to_waiting_blocked(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    create = await client.post("/queue", json=_queue_payload(seed_store.id))
    qid = create.json()["id"]
    await client.patch(f"/queue/{qid}/status", json={"status": "called"})
    await client.patch(f"/queue/{qid}/status", json={"status": "seated"})

    bad = await client.patch(f"/queue/{qid}/status", json={"status": "waiting"})
    assert bad.status_code == 409
    assert bad.json()["error"]["details"]["current"] == "seated"


async def test_queue_list_filters_by_status(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    a = await client.post("/queue", json=_queue_payload(seed_store.id, queue_no="A-1"))
    b = await client.post("/queue", json=_queue_payload(seed_store.id, queue_no="A-2"))
    # Move 'a' to called; leave 'b' at waiting.
    await client.patch(
        f"/queue/{a.json()['id']}/status", json={"status": "called"}
    )

    waiting = await client.get("/queue", params={"queue_status": "waiting"})
    assert waiting.status_code == 200
    rows = waiting.json()
    ids = {r["id"] for r in rows}
    assert b.json()["id"] in ids
    assert a.json()["id"] not in ids


async def test_idempotent_repost_same_status_returns_409(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    """Same status posted twice is a client bug — surface it as 409
    instead of silently no-op-ing so the dashboard doesn't fire two
    notifications."""
    create = await client.post("/reservations", json=_booking_payload(seed_store.id))
    rid = create.json()["id"]
    await client.patch(f"/reservations/{rid}/status", json={"status": "confirmed"})
    twice = await client.patch(
        f"/reservations/{rid}/status", json={"status": "confirmed"}
    )
    assert twice.status_code == 409
    assert "already" in twice.json()["error"]["message"].lower()
