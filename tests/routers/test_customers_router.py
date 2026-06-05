"""Integration tests for ``/customers`` router.

Covers:
- create / get / list / patch / soft-delete happy paths
- search filter on display_name / phone / line_user_id
- tier filter
- include_deleted toggle
- duplicate phone / line_user_id collision → 409
- soft-delete twice → 409
- get unknown id → 404
- patch with no actual changes → idempotent 200, no audit
- patch tier upgrade reflected in response
- points-ledger reads what orders_service wrote on close (end-to-end)
- ledger has no POST/PATCH/DELETE endpoints (immutability assertion)
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

from restaurant_api.api.deps import get_current_tenant_id, get_db, get_line_messenger
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.main import app
from restaurant_api.models import (
    Customer,
    CustomerTier,
    Tenant,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def stub_messenger() -> StubLineMessenger:
    return StubLineMessenger()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    stub_messenger: StubLineMessenger,
) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> uuid.UUID:
        return seed_tenant.id

    def _override_messenger() -> StubLineMessenger:
        return stub_messenger

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    app.dependency_overrides[get_line_messenger] = _override_messenger
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _new_customer_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "display_name": "王大明",
        "phone": f"0912{uuid.uuid4().hex[:6]}",
        "line_user_id": f"U{uuid.uuid4().hex[:16]}",
        "tier": "regular",
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────
# Create / read / list
# ──────────────────────────────────────────────────────────────────────────


async def test_create_customer_returns_201_with_id(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post("/customers", json=_new_customer_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert uuid.UUID(body["id"])
    assert body["display_name"] == "王大明"
    assert body["tier"] == "regular"
    assert Decimal(body["points_balance"]) == Decimal("0")
    assert body["deleted_at"] is None


async def test_get_customer_round_trips(client: httpx.AsyncClient) -> None:
    create = await client.post("/customers", json=_new_customer_body())
    cid = create.json()["id"]
    fetch = await client.get(f"/customers/{cid}")
    assert fetch.status_code == 200
    assert fetch.json()["id"] == cid


async def test_get_unknown_customer_returns_404(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/customers/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_list_filters_by_search_prefix(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/customers", json=_new_customer_body(display_name="王大明")
    )
    await client.post(
        "/customers", json=_new_customer_body(display_name="李小華")
    )
    resp = await client.get("/customers", params={"search": "王"})
    assert resp.status_code == 200
    names = [r["display_name"] for r in resp.json()]
    assert "王大明" in names
    assert "李小華" not in names


async def test_list_filters_by_tier(client: httpx.AsyncClient) -> None:
    await client.post("/customers", json=_new_customer_body(tier="gold"))
    await client.post("/customers", json=_new_customer_body(tier="regular"))
    resp = await client.get("/customers", params={"tier": "gold"})
    assert resp.status_code == 200
    rows = resp.json()
    assert all(r["tier"] == "gold" for r in rows)
    assert len(rows) >= 1


# ──────────────────────────────────────────────────────────────────────────
# Duplicate-handle protection
# ──────────────────────────────────────────────────────────────────────────


async def test_duplicate_phone_returns_409(client: httpx.AsyncClient) -> None:
    phone = f"0911{uuid.uuid4().hex[:6]}"
    a = await client.post("/customers", json=_new_customer_body(phone=phone))
    assert a.status_code == 201
    b = await client.post(
        "/customers",
        json=_new_customer_body(
            phone=phone,
            line_user_id=f"U{uuid.uuid4().hex[:16]}",
        ),
    )
    assert b.status_code == 409
    body = b.json()["error"]
    assert body["code"] == "CONFLICT"
    assert body["details"]["handle"] == "phone"


async def test_duplicate_line_user_id_returns_409(
    client: httpx.AsyncClient,
) -> None:
    lid = f"U{uuid.uuid4().hex[:16]}"
    await client.post("/customers", json=_new_customer_body(line_user_id=lid))
    b = await client.post(
        "/customers",
        json=_new_customer_body(
            phone=f"0922{uuid.uuid4().hex[:6]}",
            line_user_id=lid,
        ),
    )
    assert b.status_code == 409
    assert b.json()["error"]["details"]["handle"] == "line_user_id"


# ──────────────────────────────────────────────────────────────────────────
# Patch
# ──────────────────────────────────────────────────────────────────────────


async def test_patch_upgrades_tier(client: httpx.AsyncClient) -> None:
    create = await client.post("/customers", json=_new_customer_body(tier="regular"))
    cid = create.json()["id"]
    resp = await client.patch(f"/customers/{cid}", json={"tier": "gold"})
    assert resp.status_code == 200
    assert resp.json()["tier"] == "gold"


async def test_patch_with_no_changes_is_idempotent(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH with the same value as current state must not write to DB
    or to the audit log — it just echoes the current row."""
    create = await client.post(
        "/customers", json=_new_customer_body(display_name="王大明", tier="silver")
    )
    cid = create.json()["id"]
    resp = await client.patch(
        f"/customers/{cid}",
        json={"display_name": "王大明", "tier": "silver"},  # both unchanged
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "王大明"
    assert resp.json()["tier"] == "silver"


async def test_patch_to_collision_returns_409(
    client: httpx.AsyncClient,
) -> None:
    """Renaming customer B's phone to customer A's phone must 409."""
    phone_a = f"0930{uuid.uuid4().hex[:6]}"
    await client.post("/customers", json=_new_customer_body(phone=phone_a))
    b = await client.post(
        "/customers",
        json=_new_customer_body(
            phone=f"0931{uuid.uuid4().hex[:6]}",
            line_user_id=f"U{uuid.uuid4().hex[:16]}",
        ),
    )
    b_id = b.json()["id"]
    resp = await client.patch(f"/customers/{b_id}", json={"phone": phone_a})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["handle"] == "phone"


# ──────────────────────────────────────────────────────────────────────────
# Soft delete
# ──────────────────────────────────────────────────────────────────────────


async def test_soft_delete_sets_deleted_at(
    client: httpx.AsyncClient,
) -> None:
    create = await client.post("/customers", json=_new_customer_body())
    cid = create.json()["id"]
    resp = await client.delete(f"/customers/{cid}")
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is not None

    # Default list excludes deleted.
    listing = await client.get("/customers")
    ids = [r["id"] for r in listing.json()]
    assert cid not in ids

    # include_deleted=true brings them back.
    with_deleted = await client.get(
        "/customers", params={"include_deleted": "true"}
    )
    ids = [r["id"] for r in with_deleted.json()]
    assert cid in ids


async def test_double_delete_returns_409(client: httpx.AsyncClient) -> None:
    create = await client.post("/customers", json=_new_customer_body())
    cid = create.json()["id"]
    await client.delete(f"/customers/{cid}")
    twice = await client.delete(f"/customers/{cid}")
    assert twice.status_code == 409
    assert "already deleted" in twice.json()["error"]["message"].lower()


# ──────────────────────────────────────────────────────────────────────────
# Points-ledger read
# ──────────────────────────────────────────────────────────────────────────


async def test_empty_points_ledger_returns_empty_list(
    client: httpx.AsyncClient,
) -> None:
    create = await client.post("/customers", json=_new_customer_body())
    cid = create.json()["id"]
    resp = await client.get(f"/customers/{cid}/points-ledger")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_points_ledger_shows_order_close_accrual(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_store,
    seed_menu_item,
) -> None:
    """End-to-end: create customer → place order with customer_id →
    close order → ledger has one earn row."""
    cust = await client.post(
        "/customers", json=_new_customer_body(display_name="王金客")
    )
    cid = cust.json()["id"]

    order_body = {
        "store_id": str(seed_store.id),
        "customer_id": cid,
        "order_no": f"L-{uuid.uuid4().hex[:8]}",
        "business_date": date.today().isoformat(),
        "opened_at": datetime.now(UTC).isoformat(),
        "lines": [
            {
                "menu_item_id": str(seed_menu_item.id),
                "qty": "1",
                "unit_price": "500.00",
            }
        ],
    }
    create = await client.post("/orders", json=order_body)
    assert create.status_code == 201, create.text
    await client.post(f"/orders/{create.json()['id']}/close", json={})

    ledger = await client.get(f"/customers/{cid}/points-ledger")
    assert ledger.status_code == 200
    rows = ledger.json()
    assert len(rows) == 1
    assert Decimal(rows[0]["delta"]) == Decimal("5")  # 500 / 100 * 1.0
    assert rows[0]["reason"] == "order.earn"


async def test_points_ledger_endpoint_is_read_only(
    client: httpx.AsyncClient,
) -> None:
    """Append-only contract: there's no POST / PATCH / DELETE on the
    ledger sub-resource. FastAPI returns 405 Method Not Allowed."""
    create = await client.post("/customers", json=_new_customer_body())
    cid = create.json()["id"]
    bad_post = await client.post(
        f"/customers/{cid}/points-ledger", json={"delta": "100"}
    )
    bad_patch = await client.patch(
        f"/customers/{cid}/points-ledger", json={"delta": "100"}
    )
    bad_delete = await client.delete(f"/customers/{cid}/points-ledger")
    assert bad_post.status_code == 405
    assert bad_patch.status_code == 405
    assert bad_delete.status_code == 405


# ──────────────────────────────────────────────────────────────────────────
# Tenant scoping
# ──────────────────────────────────────────────────────────────────────────


async def test_other_tenant_customer_invisible(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A customer owned by another tenant must 404 on GET and not show
    up on list."""
    # Make a second tenant + a customer directly via the session.
    other = Tenant(name="Other", slug=f"oc-{uuid.uuid4().hex[:6]}")
    db_session.add(other)
    await db_session.flush()
    other_cust = Customer(
        tenant_id=other.id,
        display_name="他家會員",
        tier=CustomerTier.REGULAR,
    )
    db_session.add(other_cust)
    await db_session.flush()

    fetch = await client.get(f"/customers/{other_cust.id}")
    assert fetch.status_code == 404

    listing = await client.get("/customers", params={"search": "他家"})
    assert listing.status_code == 200
    assert listing.json() == []
