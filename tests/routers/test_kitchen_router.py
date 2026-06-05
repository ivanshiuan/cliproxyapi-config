"""Integration tests for ``/kitchen`` (KDS) router.

Covers
------
- Order creation with kitchen_station auto-stamps queued + sent_to_kitchen_at.
- GET /kitchen/queue lists routed lines, oldest first.
- GET /kitchen/queue filters by station and status.
- PATCH transitions: queued → cooking → ready → served (timestamps auto).
- Invalid transition returns 409 with allowed-list.
- Terminal status (served/cancelled) rejects further transitions.
- Line without kitchen_station returns 422 on PATCH.
- Unknown line returns 404.
- Tenant scoping: lines in another tenant aren't visible / patchable.

Shape mirrors ``test_reservations_router.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import MenuItem, Store, Tenant

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


def _order_payload(
    store_id: uuid.UUID,
    menu_item_id: uuid.UUID,
    *,
    kitchen_station: str | None = "kitchen",
    order_no: str | None = None,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "menu_item_id": str(menu_item_id),
        "qty": "1",
        "unit_price": "250.00",
    }
    if kitchen_station is not None:
        line["kitchen_station"] = kitchen_station
    return {
        "store_id": str(store_id),
        "order_no": order_no or f"T-{uuid.uuid4().hex[:6]}",
        "business_date": date.today().isoformat(),
        "opened_at": datetime.now(UTC).isoformat(),
        "lines": [line],
    }


# ──────────────────────────────────────────────────────────────────────────
# Order creation propagates KDS fields
# ──────────────────────────────────────────────────────────────────────────


async def test_order_with_kitchen_station_stamps_queued_status(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    resp = await client.post(
        "/orders",
        json=_order_payload(seed_store.id, seed_menu_item.id, kitchen_station="bar"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    line = body["lines"][0]
    assert line["kitchen_station"] == "bar"
    assert line["kitchen_status"] == "queued"
    assert line["sent_to_kitchen_at"] is not None


async def test_order_without_kitchen_station_skips_kds(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    resp = await client.post(
        "/orders",
        json=_order_payload(seed_store.id, seed_menu_item.id, kitchen_station=None),
    )
    assert resp.status_code == 201
    line = resp.json()["lines"][0]
    assert line["kitchen_station"] is None
    assert line["kitchen_status"] is None
    assert line["sent_to_kitchen_at"] is None


# ──────────────────────────────────────────────────────────────────────────
# GET /kitchen/queue
# ──────────────────────────────────────────────────────────────────────────


async def test_queue_list_returns_kds_lines_oldest_first(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    a = await client.post(
        "/orders",
        json=_order_payload(
            seed_store.id, seed_menu_item.id, kitchen_station="kitchen", order_no="K-1"
        ),
    )
    b = await client.post(
        "/orders",
        json=_order_payload(
            seed_store.id, seed_menu_item.id, kitchen_station="kitchen", order_no="K-2"
        ),
    )
    assert a.status_code == 201 and b.status_code == 201

    resp = await client.get("/kitchen/queue")
    assert resp.status_code == 200
    rows = resp.json()
    # Both lines should be present.
    order_nos = [r["order_no"] for r in rows]
    assert "K-1" in order_nos
    assert "K-2" in order_nos
    # First-in-first-out: K-1 (created first) sits ahead of K-2.
    assert order_nos.index("K-1") < order_nos.index("K-2")


async def test_queue_excludes_non_kds_lines(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    # Counter-only order — no KDS routing.
    await client.post(
        "/orders",
        json=_order_payload(
            seed_store.id, seed_menu_item.id, kitchen_station=None, order_no="C-1"
        ),
    )
    resp = await client.get("/kitchen/queue")
    assert resp.status_code == 200
    assert all(r["order_no"] != "C-1" for r in resp.json())


async def test_queue_filters_by_station(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    await client.post(
        "/orders",
        json=_order_payload(
            seed_store.id, seed_menu_item.id, kitchen_station="kitchen", order_no="K-X"
        ),
    )
    await client.post(
        "/orders",
        json=_order_payload(
            seed_store.id, seed_menu_item.id, kitchen_station="bar", order_no="B-X"
        ),
    )
    resp = await client.get("/kitchen/queue", params={"station": "bar"})
    assert resp.status_code == 200
    nos = [r["order_no"] for r in resp.json()]
    assert "B-X" in nos
    assert "K-X" not in nos


async def test_queue_filters_by_status(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    a = await client.post(
        "/orders",
        json=_order_payload(seed_store.id, seed_menu_item.id, order_no="Q-1"),
    )
    await client.post(
        "/orders",
        json=_order_payload(seed_store.id, seed_menu_item.id, order_no="Q-2"),
    )
    a_line = a.json()["lines"][0]["id"]
    # Move 'a' to cooking; leave 'b' at queued.
    await client.patch(
        f"/kitchen/lines/{a_line}/status", json={"status": "cooking"}
    )

    queued = await client.get("/kitchen/queue", params={"kitchen_status": "queued"})
    nos = [r["order_no"] for r in queued.json()]
    assert "Q-2" in nos
    assert "Q-1" not in nos


# ──────────────────────────────────────────────────────────────────────────
# PATCH /kitchen/lines/{id}/status
# ──────────────────────────────────────────────────────────────────────────


async def test_full_lifecycle_queued_to_served(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    create = await client.post(
        "/orders", json=_order_payload(seed_store.id, seed_menu_item.id)
    )
    line_id = create.json()["lines"][0]["id"]

    cook = await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "cooking"}
    )
    assert cook.status_code == 200
    body = cook.json()
    assert body["kitchen_status"] == "cooking"
    assert body["cooking_started_at"] is not None
    assert body["ready_at"] is None

    ready = await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "ready"}
    )
    assert ready.status_code == 200
    assert ready.json()["ready_at"] is not None

    served = await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "served"}
    )
    assert served.status_code == 200
    assert served.json()["served_at"] is not None


async def test_invalid_transition_returns_409(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    """queued → ready (skipping cooking) is forbidden — chef must
    acknowledge the cook step so latency analytics work."""
    create = await client.post(
        "/orders", json=_order_payload(seed_store.id, seed_menu_item.id)
    )
    line_id = create.json()["lines"][0]["id"]
    bad = await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "ready"}
    )
    assert bad.status_code == 409
    body = bad.json()["error"]
    assert body["code"] == "CONFLICT"
    assert body["details"]["current"] == "queued"
    assert "cooking" in body["details"]["allowed"]


async def test_terminal_status_blocks_further_transitions(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    create = await client.post(
        "/orders", json=_order_payload(seed_store.id, seed_menu_item.id)
    )
    line_id = create.json()["lines"][0]["id"]
    # queued → cancelled (legal); cancelled is terminal.
    await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "cancelled"}
    )
    resp = await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "cooking"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["allowed"] == []


async def test_idempotent_same_status_returns_409(
    client: httpx.AsyncClient, seed_store: Store, seed_menu_item: MenuItem
) -> None:
    create = await client.post(
        "/orders", json=_order_payload(seed_store.id, seed_menu_item.id)
    )
    line_id = create.json()["lines"][0]["id"]
    await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "cooking"}
    )
    twice = await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "cooking"}
    )
    assert twice.status_code == 409
    assert "already" in twice.json()["error"]["message"].lower()


async def test_unknown_line_returns_404(client: httpx.AsyncClient) -> None:
    resp = await client.patch(
        f"/kitchen/lines/{uuid.uuid4()}/status", json={"status": "cooking"}
    )
    assert resp.status_code == 404


async def test_non_kds_line_rejects_patch_with_422(
    client: httpx.AsyncClient,
    seed_store: Store,
    seed_menu_item: MenuItem,
    db_session: AsyncSession,
) -> None:
    """A line created without kitchen_station carries kitchen_status=None;
    progressing such a line through the KDS makes no sense — we 422."""
    create = await client.post(
        "/orders",
        json=_order_payload(seed_store.id, seed_menu_item.id, kitchen_station=None),
    )
    line_id = create.json()["lines"][0]["id"]
    resp = await client.patch(
        f"/kitchen/lines/{line_id}/status", json={"status": "cooking"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_tenant_scoping_other_tenant_line_invisible(
    client: httpx.AsyncClient,
    seed_store: Store,
    seed_menu_item: MenuItem,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """A line owned by another tenant must 404 on PATCH and not show
    up on the calling tenant's queue."""
    # Create a line under our tenant first so the queue isn't empty.
    await client.post(
        "/orders", json=_order_payload(seed_store.id, seed_menu_item.id, order_no="ME")
    )
    # Make a second tenant + store + line directly via the session.
    other_tenant = Tenant(name="Other", slug=f"o-{uuid.uuid4().hex[:6]}")
    db_session.add(other_tenant)
    await db_session.flush()
    other_store = Store(tenant_id=other_tenant.id, name="Other Store", is_active=True)
    db_session.add(other_store)
    await db_session.flush()
    from restaurant_api.models import (
        KitchenStation,
        KitchenStatus,
        MenuCategory,
        Order,
        OrderLine,
        OrderStatus,
    )

    cat = MenuCategory(
        tenant_id=other_tenant.id,
        store_id=other_store.id,
        name="X",
        sort_order=1,
        is_active=True,
    )
    db_session.add(cat)
    await db_session.flush()
    mi = MenuItem(
        tenant_id=other_tenant.id,
        store_id=other_store.id,
        category_id=cat.id,
        sku=f"X-{uuid.uuid4().hex[:6]}",
        name="X Item",
        price=Decimal("100.00"),
        cost_estimate=Decimal("30.00"),
        is_available=True,
        allergens=[],
    )
    db_session.add(mi)
    await db_session.flush()
    order = Order(
        tenant_id=other_tenant.id,
        store_id=other_store.id,
        order_no="OTHER",
        business_date=date.today(),
        opened_at=datetime.now(UTC),
        status=OrderStatus.OPEN,
    )
    db_session.add(order)
    await db_session.flush()
    line = OrderLine(
        tenant_id=other_tenant.id,
        order_id=order.id,
        menu_item_id=mi.id,
        qty=Decimal("1"),
        unit_price=Decimal("100"),
        line_total=Decimal("100"),
        kitchen_station=KitchenStation.KITCHEN,
        kitchen_status=KitchenStatus.QUEUED,
        sent_to_kitchen_at=datetime.now(UTC),
    )
    db_session.add(line)
    await db_session.flush()

    # 1. The other tenant's line must NOT appear in our queue.
    queue = await client.get("/kitchen/queue")
    assert all(r["order_no"] != "OTHER" for r in queue.json())

    # 2. PATCHing that line via our tenant DI must 404.
    resp = await client.patch(
        f"/kitchen/lines/{line.id}/status", json={"status": "cooking"}
    )
    assert resp.status_code == 404
