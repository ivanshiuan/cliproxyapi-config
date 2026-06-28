"""RBAC service — load a logged-in user's full context (roles + permissions).

The deps layer calls ``load_user_context(session, employee_id)`` once per
request after JWT decode succeeds, materializing the ``CurrentUser``
dataclass that downstream router code reads.

We could trust the permissions list baked into the JWT (it's signed),
but that loses 15 minutes of revocation freshness. PoC compromise:
- Permissions in JWT are advisory + used directly by router permission
  checks (15-min staleness window).
- ``load_user_context`` is available for endpoints that explicitly want
  fresh data (e.g. ``/auth/me``) or for revocation-sensitive checks.

This module is also the home for grant/revoke role internals; the
public endpoints land in a future ``/admin/users`` router.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    Employee,
    EmployeeRoleGrant,
    Permission,
    Role,
    RolePermission,
)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Per-request identity surface — what router code actually reads.

    Built from the JWT payload + a single DB hop (the join below) or,
    when freshness matters less, purely from JWT claims.
    """

    employee_id: uuid.UUID
    tenant_id: uuid.UUID
    employee_role: str
    functional_roles: frozenset[str]
    permissions: frozenset[str]
    jti: str
    display_name: str = ""
    email: str = ""

    def has_permission(self, name: str) -> bool:
        return name in self.permissions

    def has_role(self, name: str) -> bool:
        return name in self.functional_roles

    def has_any_role(self, names: list[str]) -> bool:
        return any(n in self.functional_roles for n in names)


async def load_user_context(
    session: AsyncSession, employee_id: uuid.UUID, *, jti: str = ""
) -> CurrentUser | None:
    """Hydrate a CurrentUser from DB. Returns None if the employee was
    deleted / disabled since the token was issued.

    Used by /auth/me and by deps when the JWT must be reconciled with
    live state (e.g. password-changed → all sessions invalidated).
    """
    emp = (
        await session.execute(
            select(Employee)
            .where(Employee.id == employee_id, Employee.deleted_at.is_(None))
            .options(selectinload(Employee.credential))
        )
    ).scalar_one_or_none()
    if emp is None:
        return None

    # Functional roles + flattened permissions in a single join.
    # We pull role rows, then permissions joined through role_permissions.
    role_rows = (
        await session.execute(
            select(Role.name)
            .join(
                EmployeeRoleGrant,
                EmployeeRoleGrant.role_id == Role.id,
            )
            .where(EmployeeRoleGrant.employee_id == employee_id)
        )
    ).all()
    role_names = frozenset(r[0] for r in role_rows)

    perm_rows = (
        await session.execute(
            select(Permission.name)
            .distinct()
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.id,
            )
            .join(Role, Role.id == RolePermission.role_id)
            .join(
                EmployeeRoleGrant,
                EmployeeRoleGrant.role_id == Role.id,
            )
            .where(EmployeeRoleGrant.employee_id == employee_id)
        )
    ).all()
    perm_names = frozenset(p[0] for p in perm_rows)

    return CurrentUser(
        employee_id=emp.id,
        tenant_id=emp.tenant_id,
        employee_role=emp.role.value if hasattr(emp.role, "value") else str(emp.role),
        functional_roles=role_names,
        permissions=perm_names,
        jti=jti,
        display_name=emp.full_name,
        email=emp.credential.email if emp.credential else "",
    )


async def grant_role(
    session: AsyncSession,
    *,
    employee_id: uuid.UUID,
    role_id: uuid.UUID,
    granted_by: uuid.UUID | None,
) -> EmployeeRoleGrant:
    """Idempotent grant — if (employee, role) already exists, returns it.

    Internal API only; the public admin endpoint that exposes this is in
    a future spec. ``granted_by=None`` denotes a system grant (bootstrap
    CLI, data migration, etc.).
    """
    existing = (
        await session.execute(
            select(EmployeeRoleGrant).where(
                EmployeeRoleGrant.employee_id == employee_id,
                EmployeeRoleGrant.role_id == role_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    grant = EmployeeRoleGrant(
        employee_id=employee_id, role_id=role_id, granted_by=granted_by
    )
    session.add(grant)
    await session.flush()
    return grant


async def revoke_role(
    session: AsyncSession,
    *,
    employee_id: uuid.UUID,
    role_id: uuid.UUID,
) -> bool:
    """Return True if a grant was removed, False if none existed."""
    grant = (
        await session.execute(
            select(EmployeeRoleGrant).where(
                EmployeeRoleGrant.employee_id == employee_id,
                EmployeeRoleGrant.role_id == role_id,
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        return False
    await session.delete(grant)
    await session.flush()
    return True


__all__ = [
    "CurrentUser",
    "grant_role",
    "load_user_context",
    "revoke_role",
]
