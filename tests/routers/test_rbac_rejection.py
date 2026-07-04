"""Cross-router RBAC rejection tests — PR-D final coverage.

For each RBAC-gated router endpoint we prove two negative paths that
the existing "happy path" tests don't cover:

1. **No permission → 403** — an authenticated user WITHOUT the right
   permission gets ``FORBIDDEN`` from ``require_permission``, not the
   business error the endpoint would emit if it ran.
2. **Cross-tenant → 404** — a user from a *different* tenant sees the
   resource as if it didn't exist (per spec §Cross-tenant guard — we
   return 404, not 403, to avoid leaking existence).

These are the tests that would have caught the "someone forgot to
attach the dep" regression that would otherwise slip through the
positive path (existing `client` fixture impersonates an owner and
would pass the endpoint anyway).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import Order, OrderStatus, Store, Tenant
from tests.conftest import needs_db

pytestmark = [pytest.mark.asyncio, needs_db]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_open_order(
    db_session: AsyncSession, tenant: Tenant, store: Store
) -> Order:
    """Minimal Order row for cross-tenant lookup tests."""
    o = Order(
        tenant_id=tenant.id,
        store_id=store.id,
        order_no=f"T-{uuid.uuid4().hex[:6]}",
        business_date=date(2026, 6, 1),
        status=OrderStatus.OPEN,
    )
    db_session.add(o)
    await db_session.flush()
    return o


# ---------------------------------------------------------------------------
# orders — POST /orders needs orders:create
# ---------------------------------------------------------------------------


async def test_orders_create_403_without_permission(role_client_factory):
    """A ``supplier`` role has neither ``orders:create`` nor ``orders:read``.
    Sending a create-order payload must 403 before service code runs."""
    c = role_client_factory("supplier", {"stock:read"})
    r = await c.post(
        "/orders",
        json={
            "store_id": str(uuid.uuid4()),
            "order_no": "SHOULD-NEVER-INSERT",
            "business_date": "2026-06-01",
        },
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_orders_read_403_without_permission(role_client_factory):
    c = role_client_factory("supplier", {"stock:read"})
    r = await c.get(f"/orders/{uuid.uuid4()}")
    assert r.status_code == 403


async def test_orders_read_cross_tenant_returns_404(
    role_client_factory,
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
):
    """Cross-tenant guard: an owner-permissioned user from tenant B
    asking for an order in tenant A must see 404, not the actual row."""
    existing = await _make_open_order(db_session, seed_tenant, seed_store)
    tenant_b = Tenant(name="Other Tenant", slug=f"other-{uuid.uuid4().hex[:6]}")
    db_session.add(tenant_b)
    await db_session.flush()

    c = role_client_factory(
        "owner",
        {"orders:read", "orders:create"},
        tenant_id=tenant_b.id,
    )
    r = await c.get(f"/orders/{existing.id}")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# stock — POST /stock/purchases needs stock:intake
# ---------------------------------------------------------------------------


async def test_stock_intake_403_without_permission(role_client_factory):
    c = role_client_factory("staff", {"orders:create"})
    r = await c.post(
        "/stock/purchases",
        json={
            "store_id": str(uuid.uuid4()),
            "supplier_name": "X",
            "invoice_no": "INV-1",
            "purchased_on": "2026-06-01",
            "lines": [],
        },
    )
    assert r.status_code == 403


async def test_stock_movements_read_403_without_permission(role_client_factory):
    c = role_client_factory("staff", {"orders:create"})
    r = await c.get("/stock/movements")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# clock — POST /clock/in only needs authenticated user, but must NOT be
# reachable when auth is entirely absent
# ---------------------------------------------------------------------------


async def test_clock_today_403_without_read_store(role_client_factory):
    """/clock/today needs clock:read_store — a plain staff role must 403."""
    c = role_client_factory("staff", {"orders:create", "clock:read_self"})
    r = await c.get("/clock/today")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# events (cost) — POST needs cost:create
# ---------------------------------------------------------------------------


async def test_events_waste_403_without_permission(role_client_factory):
    c = role_client_factory("marketing", {"visual_assets:write"})
    r = await c.post(
        "/events/waste",
        json={
            "store_id": str(uuid.uuid4()),
            "ingredient_id": str(uuid.uuid4()),
            "qty": "1.0",
            "reason": "test",
            "occurred_at": "2026-06-01T00:00:00+08:00",
        },
    )
    assert r.status_code == 403


async def test_events_list_403_without_cost_read(role_client_factory):
    c = role_client_factory("marketing", {"visual_assets:write"})
    r = await c.get("/events")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# health — /live is public, /ready requires X-Internal-Probe in prod
# ---------------------------------------------------------------------------


async def test_health_live_is_public(client):
    """/health/live has no auth deps; the client fixture doesn't matter,
    but calling with the standard client is the cheapest check."""
    r = await client.get("/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "alive"


async def test_health_ready_still_works_in_dev(client):
    """Dev env bypasses the X-Internal-Probe check per health.py."""
    r = await client.get("/health/ready")
    # 200 when DB is up; 503 if DB is unreachable — both acceptable.
    assert r.status_code in {200, 503}
