"""Integration tests for POS auth + sensitive-action gating (P1 認證/權限).

Exercises the whole chain with real tokens: PIN login → bearer token → /me,
and the 退菜/折扣 gate — denied without permission, allowed by a manager-override
PIN, and (for a manager) allowed directly. Also the admin-gated set-pin path.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.auth import AdminPrincipal, require_admin
from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import Employee, EmployeeRole, Store, Tenant
from restaurant_api.services.pin_security import hash_pin

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


async def _make_emp(
    db: AsyncSession,
    tenant: Tenant,
    store: Store,
    *,
    role: EmployeeRole,
    name: str,
    pin: str | None,
) -> Employee:
    emp = Employee(
        tenant_id=tenant.id,
        store_id=store.id,
        full_name=name,
        role=role,
        hourly_wage=Decimal("183"),
        hired_on=date(2026, 1, 1),
        pin_hash=hash_pin(pin) if pin else None,
    )
    db.add(emp)
    await db.flush()
    return emp


async def _open_line(client: httpx.AsyncClient, store: str) -> tuple[str, str]:
    """Table + item + open + one line → (session_id, line_id). Ungated setup."""
    t = (await client.post("/tables", json={"store_id": store, "name": f"A{uuid.uuid4().hex[:4]}"})).json()
    it = (
        await client.post(
            "/menu/items",
            json={"store_id": store, "sku": f"S{uuid.uuid4().hex[:5]}", "name": "品", "price": "100"},
        )
    ).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    order = (
        await client.post(
            f"/tables/sessions/{sess['id']}/lines",
            json={"menu_item_id": it["id"], "qty": "2"},
        )
    ).json()
    return sess["id"], order["lines"][0]["id"]


# ── login / me ───────────────────────────────────────────────────────────────


async def test_login_success_and_me(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    mgr = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.MANAGER, name="阿明", pin="4321")

    r = await client.post("/pos-auth/login", json={"employee_id": str(mgr.id), "pin": "4321"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["full_name"] == "阿明"
    assert "void_line" in body["permissions"] and "apply_discount" in body["permissions"]
    token = body["token"]

    me = await client.get("/pos-auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "manager"


async def test_login_wrong_pin_401(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    emp = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.STAFF, name="小華", pin="1111")
    r = await client.post("/pos-auth/login", json={"employee_id": str(emp.id), "pin": "9999"})
    assert r.status_code == 401


async def test_me_without_token_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/pos-auth/me")).status_code == 401


async def test_staff_login_has_no_sensitive_permissions(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    emp = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.STAFF, name="工讀生", pin="2222")
    body = (await client.post("/pos-auth/login", json={"employee_id": str(emp.id), "pin": "2222"})).json()
    assert body["permissions"] == []


# ── void gate ────────────────────────────────────────────────────────────────


async def test_void_denied_without_auth_403(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    _sess, line = await _open_line(client, str(seed_store.id))
    r = await client.post(f"/tables/order-lines/{line}/void", json={"reason": "x"})
    assert r.status_code == 403


async def test_void_allowed_for_manager_token(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    mgr = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.MANAGER, name="經理", pin="4321")
    token = (
        await client.post("/pos-auth/login", json={"employee_id": str(mgr.id), "pin": "4321"})
    ).json()["token"]
    _sess, line = await _open_line(client, str(seed_store.id))
    r = await client.post(
        f"/tables/order-lines/{line}/void",
        json={"reason": "客訴"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == []


async def test_void_via_manager_override(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    mgr = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.MANAGER, name="店長", pin="8888")
    _sess, line = await _open_line(client, str(seed_store.id))
    # No bearer token (acting as an unauthenticated/staff till) — a manager PIN
    # authorizes the void inline.
    r = await client.post(
        f"/tables/order-lines/{line}/void",
        json={"reason": "退", "override": {"employee_id": str(mgr.id), "pin": "8888"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == []


async def test_void_override_wrong_pin_403(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    mgr = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.MANAGER, name="店長2", pin="8888")
    _sess, line = await _open_line(client, str(seed_store.id))
    r = await client.post(
        f"/tables/order-lines/{line}/void",
        json={"override": {"employee_id": str(mgr.id), "pin": "0000"}},
    )
    assert r.status_code == 403


async def test_staff_override_rejected_no_permission(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    # A staff member's PIN can't authorize a void — they lack the permission.
    staff = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.STAFF, name="員工", pin="1234")
    _sess, line = await _open_line(client, str(seed_store.id))
    r = await client.post(
        f"/tables/order-lines/{line}/void",
        json={"override": {"employee_id": str(staff.id), "pin": "1234"}},
    )
    assert r.status_code == 403


# ── discount → checkout ──────────────────────────────────────────────────────


async def test_discount_applies_and_lowers_checkout(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    store = str(seed_store.id)
    mgr = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.MANAGER, name="M", pin="4321")
    token = (
        await client.post("/pos-auth/login", json={"employee_id": str(mgr.id), "pin": "4321"})
    ).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    sess, _line = await _open_line(client, store)  # 2 x 100 = 200

    d = await client.post(
        f"/tables/sessions/{sess}/discount",
        json={"kind": "percent", "value": "0.1"},  # 10% off
        headers=auth,
    )
    assert d.status_code == 200, d.text

    q = (await client.get(f"/tables/sessions/{sess}/quote")).json()
    assert Decimal(q["gross"]) == Decimal("200")
    assert Decimal(q["net"]) == Decimal("180")

    co = await client.post(f"/tables/sessions/{sess}/checkout", json={"tendered": "180"})
    assert co.status_code == 200
    assert Decimal(co.json()["total"]) == Decimal("180")
    assert Decimal(co.json()["change"]) == Decimal("0")


async def test_discount_denied_without_permission_403(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    sess, _line = await _open_line(client, str(seed_store.id))
    r = await client.post(
        f"/tables/sessions/{sess}/discount", json={"kind": "amount", "value": "50"}
    )
    assert r.status_code == 403


# ── admin-gated set-pin ──────────────────────────────────────────────────────


async def test_set_pin_requires_admin(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    emp = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.STAFF, name="新人", pin=None)
    r = await client.post("/pos-auth/set-pin", json={"employee_id": str(emp.id), "pin": "5555"})
    assert r.status_code == 401  # no admin session


async def test_set_pin_then_login(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    emp = await _make_emp(db_session, seed_tenant, seed_store, role=EmployeeRole.MANAGER, name="待設定", pin=None)
    app.dependency_overrides[require_admin] = lambda: AdminPrincipal()
    try:
        s = await client.post("/pos-auth/set-pin", json={"employee_id": str(emp.id), "pin": "6543"})
        assert s.status_code == 200, s.text
    finally:
        app.dependency_overrides.pop(require_admin, None)
    # the freshly-set PIN now logs in
    r = await client.post("/pos-auth/login", json={"employee_id": str(emp.id), "pin": "6543"})
    assert r.status_code == 200
