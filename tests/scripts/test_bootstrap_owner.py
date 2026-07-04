"""Tests for scripts/bootstrap_owner.py.

The CLI reads password from stdin; asserts idempotent behaviour on
credential + role grant; refuses on tenant/email mismatch.
"""

from __future__ import annotations

import io
import uuid

import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import (
    Employee,
    EmployeeRoleGrant,
    Role,
    Tenant,
    UserCredential,
)
from scripts.bootstrap_owner import BootstrapError, _read_password, bootstrap
from tests.conftest import needs_db

pytestmark = [pytest.mark.asyncio, needs_db]


# ---------------------------------------------------------------------------
# Password reader (pure — no DB)
# ---------------------------------------------------------------------------


def test_read_password_from_stdin_ok(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("MyPa55word!\n"))
    pw = _read_password(from_stdin=True)
    assert pw == "MyPa55word!"


def test_read_password_rejects_short(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("short1\n"))
    with pytest.raises(BootstrapError, match="too short"):
        _read_password(from_stdin=True)


def test_read_password_rejects_no_digit(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("onlyletters\n"))
    with pytest.raises(BootstrapError, match="letter and one digit"):
        _read_password(from_stdin=True)


def test_read_password_rejects_no_letter(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("12345678\n"))
    with pytest.raises(BootstrapError, match="letter and one digit"):
        _read_password(from_stdin=True)


# ---------------------------------------------------------------------------
# End-to-end bootstrap (DB required)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _bootstrap_kit(db_session: AsyncSession, seed_employee: Employee):
    """Yield a dict of the ids bootstrap() will consume, plus the session
    for post-assertions. bootstrap() opens its own session via
    get_sessionmaker(); we make sure the seeded rows are visible to it by
    committing the savepoint first."""
    await db_session.commit()  # make seed visible to bootstrap's session
    yield {
        "tenant_id": seed_employee.tenant_id,
        "employee_id": seed_employee.id,
        "session": db_session,
    }


async def test_bootstrap_creates_credential_and_grants_owner(_bootstrap_kit):
    await bootstrap(
        tenant_id=_bootstrap_kit["tenant_id"],
        employee_id=_bootstrap_kit["employee_id"],
        email="owner@example.com",
        password="Pa55word!",
    )
    # Fresh session to read what bootstrap committed.
    from restaurant_api.database import get_sessionmaker

    async with get_sessionmaker()() as s:
        cred = (
            await s.execute(
                select(UserCredential).where(
                    UserCredential.employee_id == _bootstrap_kit["employee_id"]
                )
            )
        ).scalar_one()
        assert cred.email == "owner@example.com"
        assert cred.password_hash.startswith("$argon2id$")

        owner_role = (
            await s.execute(
                select(Role).where(Role.name == "owner", Role.tenant_id.is_(None))
            )
        ).scalar_one()
        grant = (
            await s.execute(
                select(EmployeeRoleGrant).where(
                    EmployeeRoleGrant.employee_id == _bootstrap_kit["employee_id"],
                    EmployeeRoleGrant.role_id == owner_role.id,
                )
            )
        ).scalar_one()
        assert grant.granted_by is None  # system grant


async def test_bootstrap_idempotent_on_second_run(_bootstrap_kit):
    """Same tenant + employee + email → no error, no dup rows."""
    await bootstrap(
        tenant_id=_bootstrap_kit["tenant_id"],
        employee_id=_bootstrap_kit["employee_id"],
        email="owner@example.com",
        password="Pa55word!",
    )
    await bootstrap(
        tenant_id=_bootstrap_kit["tenant_id"],
        employee_id=_bootstrap_kit["employee_id"],
        email="owner@example.com",
        password="Pa55word!",
    )
    from restaurant_api.database import get_sessionmaker

    async with get_sessionmaker()() as s:
        creds = (
            await s.execute(
                select(UserCredential).where(
                    UserCredential.employee_id == _bootstrap_kit["employee_id"]
                )
            )
        ).all()
        assert len(creds) == 1


async def test_bootstrap_refuses_email_mismatch(_bootstrap_kit):
    await bootstrap(
        tenant_id=_bootstrap_kit["tenant_id"],
        employee_id=_bootstrap_kit["employee_id"],
        email="first@example.com",
        password="Pa55word!",
    )
    with pytest.raises(BootstrapError, match="already exists"):
        await bootstrap(
            tenant_id=_bootstrap_kit["tenant_id"],
            employee_id=_bootstrap_kit["employee_id"],
            email="different@example.com",
            password="Pa55word!",
        )


async def test_bootstrap_refuses_unknown_tenant(_bootstrap_kit):
    with pytest.raises(BootstrapError, match="tenant"):
        await bootstrap(
            tenant_id=uuid.uuid4(),
            employee_id=_bootstrap_kit["employee_id"],
            email="x@example.com",
            password="Pa55word!",
        )


async def test_bootstrap_refuses_unknown_employee(_bootstrap_kit):
    with pytest.raises(BootstrapError, match="employee"):
        await bootstrap(
            tenant_id=_bootstrap_kit["tenant_id"],
            employee_id=uuid.uuid4(),
            email="x@example.com",
            password="Pa55word!",
        )


async def test_bootstrap_refuses_cross_tenant_employee(
    db_session: AsyncSession, _bootstrap_kit
):
    """Employee in tenant A, but bootstrap invoked with tenant B."""
    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
    db_session.add(other)
    await db_session.commit()

    with pytest.raises(BootstrapError, match="belongs to tenant"):
        await bootstrap(
            tenant_id=other.id,
            employee_id=_bootstrap_kit["employee_id"],
            email="x@example.com",
            password="Pa55word!",
        )
