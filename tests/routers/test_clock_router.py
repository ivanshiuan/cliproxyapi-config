"""Integration tests for the ``/clock`` router.

Strategy
========
- Mount the router onto the shared FastAPI ``app`` once per session, since
  ``main.py`` doesn't include it yet (file-ownership boundary).
- Use the rolled-back ``db_session`` from ``tests/conftest.py``.
- Drive endpoints via a module-local ``client`` fixture
  (``httpx.AsyncClient`` over ASGITransport — shares the test's event
  loop so asyncpg futures don't end up cross-loop).
- Override ``get_is_holiday`` per-test to deterministically pin the
  holiday flag (so tests aren't flaky on weekends).

The 10 cases below map 1-to-1 against the AC list in
``specs/clock_router.md`` plus a few of the listed test requirements.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from restaurant_api.api.deps import get_db
from restaurant_api.config import get_settings
from restaurant_api.main import app
from restaurant_api.models import Employee, LeaveRequest, Store, TimeClock
from restaurant_api.routers.clock import get_is_holiday
from restaurant_api.routers.clock import router as clock_router


# Override the session-scoped ``_engine`` fixture from conftest with a
# function-scoped one. pytest-asyncio 1.x rejects session-scoped async
# fixtures unless they declare ``loop_scope="session"``; the project's
# conftest hasn't been updated yet, and this module isn't allowed to
# touch it. Function-scope is fine: engine creation is cheap.
@pytest_asyncio.fixture
async def _engine():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


# Override ``client`` from conftest. The shared fixture uses sync
# ``TestClient`` which runs the ASGI app on a separate event loop via
# anyio's blocking portal — that conflicts with the async DB session
# fixture's loop (asyncpg futures end up on the wrong loop). We replace
# it with ``httpx.AsyncClient`` over ``ASGITransport``, which shares the
# test's loop.
@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    async def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ──────────────────────────────────────────────────────────────────────────
# One-time router mount
# ──────────────────────────────────────────────────────────────────────────


_MOUNTED = False


@pytest.fixture(autouse=True)
def _mount_router() -> Iterator[None]:
    """Idempotently attach ``/clock`` (+ the shared error handler) to the app.

    ``main.py`` now mounts the clock router in prod wiring. This fixture
    predates that — it stays as a defensive no-op for environments where
    main hasn't run, but it must check route paths first to avoid a
    duplicate include (which would trigger FastAPI's "Duplicate Operation
    ID" warnings).
    """
    global _MOUNTED
    if not _MOUNTED:
        from restaurant_api.api.errors import DomainError, domain_error_handler

        already_mounted = any(
            getattr(r, "path", "").startswith("/clock") for r in app.routes
        )
        if not already_mounted:
            app.include_router(clock_router)
        app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]
        _MOUNTED = True
    # Default holiday override: never a holiday — most tests want the
    # regular/OT bucketing path. Specific tests override per-test below.
    app.dependency_overrides[get_is_holiday] = lambda: (lambda d: False)
    yield
    app.dependency_overrides.pop(get_is_holiday, None)


# ──────────────────────────────────────────────────────────────────────────
# Local helpers
# ──────────────────────────────────────────────────────────────────────────


def _iso(dt: datetime) -> str:
    """ISO-8601 string with explicit UTC offset (what clients send)."""
    return dt.astimezone(UTC).isoformat()


async def _seed_open_clock(
    db: AsyncSession,
    *,
    employee: Employee,
    store: Store,
    clock_in_at: datetime,
) -> TimeClock:
    """Insert a still-open ``time_clocks`` row directly (no HTTP)."""
    row = TimeClock(
        tenant_id=employee.tenant_id,
        store_id=store.id,
        employee_id=employee.id,
        clock_in=clock_in_at,
        clock_out=None,
    )
    db.add(row)
    await db.flush()
    return row


# ──────────────────────────────────────────────────────────────────────────
# Tests — numbered to match the task brief / AC list
# ──────────────────────────────────────────────────────────────────────────


# AC-1
async def test_clock_in_creates_row_with_null_clock_out(
    client, db_session, seed_employee, seed_store
):
    """POST /clock/in writes a row with clock_out=NULL and zero buckets."""
    resp = await client.post(
        "/clock/in",
        json={
            "employee_id": str(seed_employee.id),
            "store_id": str(seed_store.id),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["employee_id"] == str(seed_employee.id)
    assert body["store_id"] == str(seed_store.id)

    row = (
        await db_session.execute(
            select(TimeClock).where(TimeClock.id == uuid.UUID(body["time_clock_id"]))
        )
    ).scalar_one()
    assert row.clock_out is None
    assert row.regular_hours == Decimal("0")
    assert row.overtime_tier1_hours == Decimal("0")
    assert row.overtime_tier2_hours == Decimal("0")
    assert row.holiday_hours == Decimal("0")


# AC-2
async def test_double_clock_in_returns_409(
    client, seed_employee, seed_store
):
    """Two POST /clock/in for the same employee → second is 409 already_clocked_in."""
    body = {
        "employee_id": str(seed_employee.id),
        "store_id": str(seed_store.id),
    }
    first = await client.post("/clock/in", json=body)
    assert first.status_code == 201

    second = await client.post("/clock/in", json=body)
    assert second.status_code == 409
    payload = second.json()
    assert payload["error"]["code"] == "CONFLICT"
    assert payload["error"]["details"]["code"] == "already_clocked_in"
    # The conflict response carries the existing row's id.
    assert payload["error"]["details"]["time_clock_id"] == first.json()["time_clock_id"]


# AC-4
async def test_clock_out_without_clock_in_returns_409(
    client, seed_employee
):
    """POST /clock/out with no open row → 409 not_clocked_in."""
    resp = await client.post(
        "/clock/out",
        json={"employee_id": str(seed_employee.id)},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["details"]["code"] == "not_clocked_in"


# Hour-bucket: 6h shift → all regular.
async def test_clock_out_6h_all_regular(
    client, db_session, seed_employee, seed_store
):
    in_at = datetime.now(UTC) - timedelta(hours=6)
    await _seed_open_clock(
        db_session, employee=seed_employee, store=seed_store, clock_in_at=in_at
    )

    resp = await client.post(
        "/clock/out",
        json={"employee_id": str(seed_employee.id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["regular_hours"]) == Decimal("6.00")
    assert Decimal(body["overtime_tier1_hours"]) == Decimal("0.00")
    assert Decimal(body["overtime_tier2_hours"]) == Decimal("0.00")
    assert Decimal(body["holiday_hours"]) == Decimal("0.00")
    assert Decimal(body["total_hours"]) == Decimal("6.00")


# A fixed UTC moment that maps to a mid-evening TPE time so a 9/11/13h shift
# back from this anchor is guaranteed to stay within ONE calendar day in TPE.
# The labor classifier splits hours by TPE calendar day; without this anchor
# the test would flap any time CI runs around midnight TPE.
# 2026-06-04 14:00 UTC == 2026-06-04 22:00 TPE — a 13h back lands at 09:00 TPE.
_FIXED_NOW_UTC = datetime(2026, 6, 4, 14, 0, 0, tzinfo=UTC)


def _freeze_clock_now():
    """Patch _utcnow() in clock_service to return the fixed anchor.

    Returns a context manager. Use as: ``with _freeze_clock_now(): ...``
    """
    return patch(
        "restaurant_api.services.clock_service._utcnow",
        return_value=_FIXED_NOW_UTC,
    )


# Hour-bucket: 9h shift → 8 regular + 1 OT1.
async def test_clock_out_9h_regular_plus_ot1(
    client, db_session, seed_employee, seed_store
):
    in_at = _FIXED_NOW_UTC - timedelta(hours=9)
    await _seed_open_clock(
        db_session, employee=seed_employee, store=seed_store, clock_in_at=in_at
    )

    with _freeze_clock_now():
        resp = await client.post(
            "/clock/out",
            json={"employee_id": str(seed_employee.id)},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["regular_hours"]) == Decimal("8.00")
    assert Decimal(body["overtime_tier1_hours"]) == Decimal("1.00")
    assert Decimal(body["overtime_tier2_hours"]) == Decimal("0.00")
    assert Decimal(body["total_hours"]) == Decimal("9.00")


# Hour-bucket: 11h shift → 8 + 2 + 1.
async def test_clock_out_11h_fills_ot1_and_ot2(
    client, db_session, seed_employee, seed_store
):
    in_at = _FIXED_NOW_UTC - timedelta(hours=11)
    await _seed_open_clock(
        db_session, employee=seed_employee, store=seed_store, clock_in_at=in_at
    )

    with _freeze_clock_now():
        resp = await client.post(
            "/clock/out",
            json={"employee_id": str(seed_employee.id)},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["regular_hours"]) == Decimal("8.00")
    assert Decimal(body["overtime_tier1_hours"]) == Decimal("2.00")
    assert Decimal(body["overtime_tier2_hours"]) == Decimal("1.00")
    assert Decimal(body["total_hours"]) == Decimal("11.00")


# Hour-bucket: 13h shift → exceeds 12h cap → 422.
async def test_clock_out_13h_rejected_over_12h_cap(
    client, db_session, seed_employee, seed_store
):
    in_at = _FIXED_NOW_UTC - timedelta(hours=13)
    await _seed_open_clock(
        db_session, employee=seed_employee, store=seed_store, clock_in_at=in_at
    )

    with _freeze_clock_now():
        resp = await client.post(
            "/clock/out",
            json={"employee_id": str(seed_employee.id)},
        )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["details"]["code"] == "hours_over_cap"


# AC-8: leave request happy path.
async def test_leave_request_happy_path(
    client, db_session, seed_employee
):
    start = datetime(2026, 6, 1, 1, 0, tzinfo=UTC)  # TPE 09:00
    end = start + timedelta(hours=8)
    resp = await client.post(
        "/clock/leave-request",
        json={
            "employee_id": str(seed_employee.id),
            "leave_type": "特休",
            "start_at": _iso(start),
            "end_at": _iso(end),
            "hours": "8.00",
            "reason": "Family trip",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["leave_type"] == "特休"
    assert Decimal(body["hours"]) == Decimal("8.00")

    row = (
        await db_session.execute(
            select(LeaveRequest).where(LeaveRequest.id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert row.approved_by is None
    assert row.approved_at is None


# AC-10/11: /clock/today returns currently-clocked-in only.
async def test_today_returns_open_clocks_only(
    client, db_session, seed_tenant, seed_store, seed_employee
):
    # Anchor to the frozen test clock (_FIXED_NOW_UTC) so BOTH "today" (TPE
    # date) and elapsed_hours are deterministic. With the real wall-clock, a
    # clock-in "minutes ago" within ~10 min after TPE midnight (UTC 16:00-16:10)
    # lands on the *previous* TPE day and gets dropped from "today" — a genuine
    # ~daily CI flake. Freezing _utcnow (as the sibling clock-out tests do)
    # removes the dependency entirely.
    in_at = _FIXED_NOW_UTC - timedelta(minutes=10)
    await _seed_open_clock(
        db_session, employee=seed_employee, store=seed_store, clock_in_at=in_at
    )

    # A second employee who already clocked out today — must NOT appear.
    from datetime import date

    from restaurant_api.models import EmployeeRole

    closed_emp = Employee(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        full_name="Closed Clocker",
        role=EmployeeRole.STAFF,
        hourly_wage=Decimal("200.00"),
        hired_on=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(closed_emp)
    await db_session.flush()
    closed_row = TimeClock(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        employee_id=closed_emp.id,
        clock_in=in_at - timedelta(hours=1),
        clock_out=_FIXED_NOW_UTC - timedelta(minutes=10),
        regular_hours=Decimal("3.00"),
    )
    db_session.add(closed_row)
    await db_session.flush()

    with _freeze_clock_now():
        resp = await client.get(f"/clock/today?store_id={seed_store.id}")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    assert items[0]["employee_id"] == str(seed_employee.id)
    # Elapsed > 5 minutes since we clocked-in 10 minutes ago.
    assert Decimal(items[0]["elapsed_hours"]) > Decimal("0.05")


# Optional AC-7: holiday override puts everything into holiday_hours.
async def test_holiday_override_routes_hours_to_holiday_bucket(
    client, db_session, seed_employee, seed_store
):
    app.dependency_overrides[get_is_holiday] = lambda: (lambda d: True)
    try:
        in_at = _FIXED_NOW_UTC - timedelta(hours=10)
        await _seed_open_clock(
            db_session, employee=seed_employee, store=seed_store, clock_in_at=in_at
        )
        with _freeze_clock_now():
            resp = await client.post(
                "/clock/out",
                json={"employee_id": str(seed_employee.id)},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Decimal(body["regular_hours"]) == Decimal("0.00")
        assert Decimal(body["overtime_tier1_hours"]) == Decimal("0.00")
        assert Decimal(body["overtime_tier2_hours"]) == Decimal("0.00")
        assert Decimal(body["holiday_hours"]) == Decimal("10.00")
        assert Decimal(body["total_hours"]) == Decimal("10.00")
    finally:
        # Restore the "never a holiday" default for the rest of the suite.
        app.dependency_overrides[get_is_holiday] = lambda: (lambda d: False)
