"""POS sensitive-action permission matrix (role-based).

iCHEF's "操作關卡": normal ordering / cash checkout is open to any clocked-in
staff, but a few actions that move money or erase records are gated —
退菜 (void a sent line) and 折扣 (apply a discount). Owners and managers hold
those permissions; everyone else must get a manager to authorize inline
(manager-override PIN), which is recorded in the audit trail.

Kept intentionally small and declarative so it can grow into a full
role→permission table later without changing call sites.
"""

from __future__ import annotations

import enum

from ..models import EmployeeRole


class SensitiveAction(enum.StrEnum):
    VOID_LINE = "void_line"          # 退菜
    APPLY_DISCOUNT = "apply_discount"  # 折扣


# Roles that may perform each action without a manager override.
_ROLE_PERMISSIONS: dict[EmployeeRole, set[SensitiveAction]] = {
    EmployeeRole.OWNER: {SensitiveAction.VOID_LINE, SensitiveAction.APPLY_DISCOUNT},
    EmployeeRole.MANAGER: {SensitiveAction.VOID_LINE, SensitiveAction.APPLY_DISCOUNT},
    EmployeeRole.CHEF: set(),
    EmployeeRole.STAFF: set(),
    EmployeeRole.PARTTIME: set(),
}


def role_can(role: EmployeeRole, action: SensitiveAction) -> bool:
    return action in _ROLE_PERMISSIONS.get(role, set())


def permissions_for(role: EmployeeRole) -> list[str]:
    """Serialisable list of a role's permissions (for the /me endpoint / UI)."""
    return sorted(a.value for a in _ROLE_PERMISSIONS.get(role, set()))


__all__ = ["SensitiveAction", "permissions_for", "role_can"]
