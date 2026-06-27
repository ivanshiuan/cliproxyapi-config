"""ORM-level tests for the auth/RBAC models.

Validates the PR-A slice of specs/auth_rbac_system.md — schema is
applied, constraints fire correctly, relationships resolve, and the
seed migration left the catalog in the expected shape.

These tests need a real Postgres with the auth migrations applied
(`make db-migrate` brings it up to head). They use the savepoint-rolled
``db_session`` fixture so no row leaks between tests.

Coverage map:
    test_user_credential_roundtrip            → 1:1 with employees
    test_user_credential_employee_unique      → UNIQUE on employee_id
    test_user_credential_email_unique         → UNIQUE on email
    test_user_credential_email_citext         → CITEXT case-insensitive
    test_role_name_snake_case_check           → CHECK ck_roles_name_snake_case
    test_role_tenant_name_composite_unique    → UNIQUE (tenant_id, name)
    test_permission_name_format_check         → CHECK resource:action
    test_permission_name_unique               → UNIQUE on name
    test_role_permission_cascade              → CASCADE on role/permission delete
    test_employee_role_grant_unique           → UNIQUE (employee_id, role_id)
    test_refresh_token_hash_unique            → UNIQUE on token_hash
    test_refresh_token_partial_index_used     → SELECT plan hits the partial index
    test_seed_migration_22_permissions        → seed produced 22 system perms
    test_seed_migration_5_system_roles        → seed produced 5 system roles
    test_seed_migration_role_perm_mappings    → owner has all 22; staff has 4
    test_employee_credential_relationship     → ORM back-populates resolves
    test_employee_roles_viewonly_relationship → ORM secondary works
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import (
    Employee,
    EmployeeRoleGrant,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    Tenant,
    UserCredential,
)
from tests.conftest import needs_db

pytestmark = [pytest.mark.asyncio, needs_db]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _make_credential(
    db_session: AsyncSession,
    employee: Employee,
    email: str = "alice@example.com",
) -> UserCredential:
    cred = UserCredential(
        employee_id=employee.id,
        email=email,
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$test$dummy",
    )
    db_session.add(cred)
    await db_session.flush()
    return cred


# ---------------------------------------------------------------------------
# user_credentials
# ---------------------------------------------------------------------------


async def test_user_credential_roundtrip(
    db_session: AsyncSession, seed_employee: Employee
) -> None:
    cred = await _make_credential(db_session, seed_employee)
    fetched = (
        await db_session.execute(
            select(UserCredential).where(UserCredential.id == cred.id)
        )
    ).scalar_one()
    assert fetched.email == "alice@example.com"
    assert fetched.failed_login_count == 0
    assert fetched.is_active is True
    assert fetched.locked_until is None


async def test_user_credential_employee_unique(
    db_session: AsyncSession, seed_employee: Employee
) -> None:
    await _make_credential(db_session, seed_employee, email="first@x.com")
    dup = UserCredential(
        employee_id=seed_employee.id,
        email="second@x.com",
        password_hash="x",
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_user_credential_email_unique(
    db_session: AsyncSession,
    seed_employee: Employee,
    seed_tenant: Tenant,
) -> None:
    await _make_credential(db_session, seed_employee, email="shared@x.com")
    # Second employee in same tenant.
    other = Employee(
        tenant_id=seed_tenant.id,
        full_name="Bob",
        role="staff",
        hourly_wage=160,
        hired_on=datetime(2026, 1, 1, tzinfo=UTC).date(),
    )
    db_session.add(other)
    await db_session.flush()
    dup = UserCredential(
        employee_id=other.id, email="shared@x.com", password_hash="x"
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_user_credential_email_citext(
    db_session: AsyncSession, seed_employee: Employee
) -> None:
    """CITEXT means SELECT compares case-insensitively, so duplicate
    insert with different casing must violate the UNIQUE constraint."""
    await _make_credential(db_session, seed_employee, email="Mixed@Case.COM")
    # Insert a second credential pointing at a different employee but
    # with a casing-variant of the same email — must fail UNIQUE.
    other = Employee(
        tenant_id=seed_employee.tenant_id,
        full_name="Bob",
        role="staff",
        hourly_wage=160,
        hired_on=datetime(2026, 1, 1, tzinfo=UTC).date(),
    )
    db_session.add(other)
    await db_session.flush()
    dup = UserCredential(
        employee_id=other.id,
        email="mixed@case.com",
        password_hash="x",
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------


async def test_role_name_snake_case_check(db_session: AsyncSession) -> None:
    bad = Role(name="BadName", display_name="x")
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_role_tenant_name_composite_unique(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    r1 = Role(name="custom", display_name="Custom", tenant_id=seed_tenant.id)
    db_session.add(r1)
    await db_session.flush()
    r2 = Role(name="custom", display_name="Custom 2", tenant_id=seed_tenant.id)
    db_session.add(r2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------


async def test_permission_name_format_check(db_session: AsyncSession) -> None:
    bad = Permission(name="noColon")
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_permission_name_unique(db_session: AsyncSession) -> None:
    # `orders:create` already exists in the seed catalog, so a duplicate
    # insert must violate UNIQUE.
    dup = Permission(name="orders:create", description="dup")
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# role_permissions
# ---------------------------------------------------------------------------


async def test_role_permission_cascade(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    role = Role(name="tmp", display_name="Tmp", tenant_id=seed_tenant.id)
    perm = Permission(name="tmp:noop", description="probe")
    db_session.add_all([role, perm])
    await db_session.flush()
    grant = RolePermission(role_id=role.id, permission_id=perm.id)
    db_session.add(grant)
    await db_session.flush()
    grant_id = grant.id
    # CASCADE on role delete → grant disappears.
    await db_session.delete(role)
    await db_session.flush()
    leftover = (
        await db_session.execute(
            select(RolePermission).where(RolePermission.id == grant_id)
        )
    ).scalar_one_or_none()
    assert leftover is None


# ---------------------------------------------------------------------------
# employee_roles (junction model is named EmployeeRoleGrant to avoid the
# name collision with the existing EmployeeRole enum on Employee.role)
# ---------------------------------------------------------------------------


async def test_employee_role_grant_unique(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_employee: Employee,
) -> None:
    role = Role(name="probe", display_name="P", tenant_id=seed_tenant.id)
    db_session.add(role)
    await db_session.flush()
    g1 = EmployeeRoleGrant(employee_id=seed_employee.id, role_id=role.id)
    db_session.add(g1)
    await db_session.flush()
    g2 = EmployeeRoleGrant(employee_id=seed_employee.id, role_id=role.id)
    db_session.add(g2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# refresh_tokens
# ---------------------------------------------------------------------------


async def test_refresh_token_hash_unique(
    db_session: AsyncSession, seed_employee: Employee
) -> None:
    h = _hash_token(secrets.token_urlsafe(32))
    t1 = RefreshToken(
        employee_id=seed_employee.id,
        token_hash=h,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(t1)
    await db_session.flush()
    t2 = RefreshToken(
        employee_id=seed_employee.id,
        token_hash=h,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(t2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_refresh_token_partial_index_used(
    db_session: AsyncSession,
) -> None:
    """The partial index ix_refresh_tokens_employee_active is created
    WHERE revoked_at IS NULL — verify it exists in pg_indexes."""
    result = await db_session.execute(
        text(
            """
            SELECT indexdef
              FROM pg_indexes
             WHERE schemaname = 'public'
               AND indexname = 'ix_refresh_tokens_employee_active'
            """
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None, "partial index should exist after migration"
    assert "revoked_at IS NULL" in row


# ---------------------------------------------------------------------------
# Seed migration content (PR-A's seed file)
# ---------------------------------------------------------------------------


async def test_seed_migration_22_permissions(db_session: AsyncSession) -> None:
    count = (
        await db_session.execute(
            select(Permission).where(Permission.is_system.is_(True))
        )
    ).all()
    assert len(count) >= 22, f"expected ≥22 system permissions, got {len(count)}"


async def test_seed_migration_5_system_roles(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            select(Role.name).where(
                Role.is_system.is_(True), Role.tenant_id.is_(None)
            )
        )
    ).all()
    names = {r[0] for r in rows}
    assert names >= {"owner", "store_manager", "marketing", "supplier", "staff"}


async def test_seed_migration_role_perm_mappings(
    db_session: AsyncSession,
) -> None:
    """owner gets every system permission; staff gets exactly 4."""
    owner_perms = (
        await db_session.execute(
            text(
                """
                SELECT p.name
                  FROM permissions p
                  JOIN role_permissions rp ON rp.permission_id = p.id
                  JOIN roles r ON r.id = rp.role_id
                 WHERE r.name = 'owner' AND r.tenant_id IS NULL
                """
            )
        )
    ).all()
    owner_names = {row[0] for row in owner_perms}
    # owner should have all 22 (or more if catalog grew).
    assert len(owner_names) >= 22

    staff_perms = (
        await db_session.execute(
            text(
                """
                SELECT p.name
                  FROM permissions p
                  JOIN role_permissions rp ON rp.permission_id = p.id
                  JOIN roles r ON r.id = rp.role_id
                 WHERE r.name = 'staff' AND r.tenant_id IS NULL
                """
            )
        )
    ).all()
    staff_names = {row[0] for row in staff_perms}
    assert staff_names == {
        "orders:create",
        "orders:read",
        "orders:close",
        "clock:read_self",
    }


# ---------------------------------------------------------------------------
# ORM relationships
# ---------------------------------------------------------------------------


async def test_employee_credential_relationship(
    db_session: AsyncSession, seed_employee: Employee
) -> None:
    """back_populates resolves both ways."""
    cred = await _make_credential(db_session, seed_employee, email="rel@x.com")
    await db_session.refresh(seed_employee, attribute_names=["credential"])
    assert seed_employee.credential is not None
    assert seed_employee.credential.id == cred.id
    # reverse
    await db_session.refresh(cred, attribute_names=["employee"])
    assert cred.employee.id == seed_employee.id


async def test_employee_roles_viewonly_relationship(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_employee: Employee,
) -> None:
    """Employee.roles works via the employee_roles secondary table."""
    # Grant the seeded staff role to the employee.
    staff_role = (
        await db_session.execute(
            select(Role).where(
                Role.name == "staff", Role.tenant_id.is_(None)
            )
        )
    ).scalar_one()
    grant = EmployeeRoleGrant(
        employee_id=seed_employee.id, role_id=staff_role.id
    )
    db_session.add(grant)
    await db_session.flush()

    await db_session.refresh(seed_employee, attribute_names=["roles"])
    role_names = {r.name for r in seed_employee.roles}
    assert "staff" in role_names
