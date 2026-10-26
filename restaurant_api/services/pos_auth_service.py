"""POS employee auth + sensitive-action authorization.

Three jobs:
  - ``set_pin``   : admin sets/rotates an employee's PIN (hash stored, raw dropped).
  - ``login``     : verify PIN → the floor principal the router mints a token for.
  - ``authorize_sensitive`` : gate 退菜/折扣 — allow if the actor's role holds the
    permission, else require a manager-override PIN, else 403. Returns *who*
    authorized and *how* (self vs override) so the caller can audit it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import AuthenticationError, AuthorizationError, ValidationError
from ..api.pos_auth import PosPrincipal
from ..models import Employee
from .audit_service import audit
from .permissions import SensitiveAction, role_can
from .pin_security import hash_pin, verify_pin


async def _load_employee(
    session: AsyncSession, employee_id: uuid.UUID, tenant_id: uuid.UUID
) -> Employee:
    row = (
        await session.execute(
            select(Employee).where(
                Employee.id == employee_id,
                Employee.tenant_id == tenant_id,
                Employee.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AuthenticationError("員工不存在或無權限")
    return row


async def list_employees(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
) -> list[Employee]:
    """Active employees at a store — powers the pre-login picker. No secrets."""
    return list(
        (
            await session.execute(
                select(Employee)
                .where(
                    Employee.tenant_id == tenant_id,
                    Employee.store_id == store_id,
                    Employee.deleted_at.is_(None),
                    Employee.is_active.is_(True),
                )
                .order_by(Employee.full_name)
            )
        ).scalars().all()
    )


async def set_pin(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    pin: str,
) -> None:
    emp = await _load_employee(session, employee_id, tenant_id)
    emp.pin_hash = hash_pin(pin)
    await session.flush()
    await audit(
        session,
        action="employee.pin_set",
        tenant_id=tenant_id,
        store_id=emp.store_id,
        actor_id=emp.id,
        target=("employees", emp.id),
        # NB: never audit the PIN itself — only that it changed.
        after={"pin_set": True},
    )


async def login(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    pin: str,
) -> tuple[PosPrincipal, str]:
    """Verify PIN; return (principal, full_name). Raises 401 on bad credentials."""
    emp = await _load_employee(session, employee_id, tenant_id)
    if not emp.is_active or not verify_pin(pin, emp.pin_hash):
        raise AuthenticationError("PIN 錯誤或帳號未啟用")
    if emp.store_id is None:
        raise ValidationError("此員工尚未指派門市,無法登入 POS")
    principal = PosPrincipal(employee_id=emp.id, store_id=emp.store_id, role=emp.role)
    return principal, emp.full_name


async def authorize_sensitive(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    principal: PosPrincipal | None,
    action: SensitiveAction,
    override_employee_id: uuid.UUID | None = None,
    override_pin: str | None = None,
) -> tuple[uuid.UUID, str]:
    """Return (authorizing_employee_id, via) for a permitted sensitive action.

    ``via`` is ``"self"`` (the logged-in actor holds the permission) or
    ``"override"`` (a manager entered their PIN). Raises 403 otherwise.
    """
    if principal is not None and role_can(principal.role, action):
        return principal.employee_id, "self"

    if override_employee_id is not None and override_pin is not None:
        manager = await _load_employee(session, override_employee_id, tenant_id)
        if (
            manager.is_active
            and role_can(manager.role, action)
            and verify_pin(override_pin, manager.pin_hash)
        ):
            return manager.id, "override"
        raise AuthorizationError("主管覆核失敗:PIN 錯誤或該員工無此權限")

    raise AuthorizationError("此動作需要權限,或由主管輸入 PIN 覆核")


__all__ = ["authorize_sensitive", "list_employees", "login", "set_pin"]
