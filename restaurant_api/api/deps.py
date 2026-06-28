"""FastAPI dependency injection helpers shared across routers.

These let business code declare what it needs (DB session, current tenant,
current user, role / permission gate) without each router file repeating
the wiring.

Auth additions (Phase 2 — see specs/auth_rbac_system.md):
- ``get_current_user`` parses Authorization: Bearer <jwt>
- ``require_role``, ``require_any_role``, ``require_permission`` factories
- ``get_current_tenant_id`` now prefers JWT, header-fallback gated by env
- ``assert_tenant_match`` helper for cross-tenant guard
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session as _get_session
from ..integrations.line import LineMessenger
from ..integrations.line import get_messenger as _get_messenger
from ..security.jwt import JwtError, JwtPayload, decode_access_token
from .errors import AuthError, ForbiddenError, NotFoundError

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# DB session
# ──────────────────────────────────────────────────────────────────────────


async def get_db() -> AsyncIterator[AsyncSession]:
    """Async DB session with commit-on-success / rollback-on-error.

    Services only ``flush()`` (so they can use the returned PK before commit);
    the request-level transaction boundary lives here. If the handler
    returns successfully, we commit; if it raised, we roll back.
    Tests override this with the savepoint-rolled-back fixture so this
    commit never reaches the DB during pytest.
    """
    async for session in _get_session():
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


# ──────────────────────────────────────────────────────────────────────────
# LINE messenger
# ──────────────────────────────────────────────────────────────────────────


def get_line_messenger() -> LineMessenger:
    """LINE messenger DI seam — tests can override with their own stub."""
    return _get_messenger()


def get_line_channel_secret() -> str:
    """HMAC key for inbound LINE webhook signature verification.

    Empty until ``LINE_CHANNEL_SECRET`` is configured, in which case the
    webhook refuses requests (503) rather than accept unverifiable events.
    """
    return get_settings().line_channel_secret


# ──────────────────────────────────────────────────────────────────────────
# Current-user / RBAC dependencies
# ──────────────────────────────────────────────────────────────────────────

# Imported lazily inside the dep to avoid a circular at module load time
# (rbac_service → models → ... → api/deps.py — we break the cycle here).
# The dataclass lives in services.rbac_service so it can be reused outside
# the FastAPI request lifecycle (CLIs, background jobs).


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    """Parse Authorization header → JWT → CurrentUser. Cache on request.state.

    Modes (from ``settings.auth_enforcement``):
    - ``off``:     never raise; returns None.
    - ``warn``:    parse if present; log warning if absent; returns None
                   when absent so legacy header-tenant routers still work.
    - ``enforce``: require a valid JWT; raises AuthError(401) otherwise.

    The return type is intentionally ``CurrentUser | None``. Routers that
    require an authenticated user should chain through ``require_permission``
    or ``require_role`` (which raise on None).
    """
    # Late import — see module-level comment.
    from ..services.rbac_service import CurrentUser

    cached = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached

    settings = get_settings()
    token = _extract_bearer(authorization)

    if token is None:
        if settings.auth_enforcement == "enforce":
            raise AuthError("missing bearer token")
        if settings.auth_enforcement == "warn":
            logger.warning(
                "auth.missing_token",
                extra={"path": str(request.url.path)},
            )
        return None

    try:
        payload: JwtPayload = decode_access_token(token)
    except JwtError as exc:
        if settings.auth_enforcement == "off":
            return None
        raise AuthError(str(exc)) from exc

    user = CurrentUser(
        employee_id=payload.sub,
        tenant_id=payload.tenant_id,
        employee_role=payload.employee_role,
        functional_roles=frozenset(payload.roles),
        permissions=frozenset(payload.permissions),
        jti=payload.jti,
    )
    request.state.current_user = user
    return user


def require_role(role_name: str) -> Callable:
    """Factory: returns a dep that asserts ``role_name`` is among the
    user's functional_roles. Raises 401 if no user, 403 if role missing.
    """

    async def _dep(user=Depends(get_current_user)):
        if user is None:
            raise AuthError("authentication required")
        if role_name not in user.functional_roles:
            raise ForbiddenError(
                f"role required: {role_name}",
                details={"required_role": role_name},
            )
        return user

    return _dep


def require_any_role(role_names: list[str]) -> Callable:
    """Factory: returns a dep that asserts ANY of ``role_names`` matches."""
    required = list(role_names)

    async def _dep(user=Depends(get_current_user)):
        if user is None:
            raise AuthError("authentication required")
        if not any(r in user.functional_roles for r in required):
            raise ForbiddenError(
                "role required: one of " + ", ".join(required),
                details={"required_roles": required},
            )
        return user

    return _dep


def require_permission(perm_name: str) -> Callable:
    """Factory: returns a dep that asserts ``perm_name`` is in
    user.permissions. Raises 401 if no user, 403 if perm missing.
    """

    async def _dep(user=Depends(get_current_user)):
        if user is None:
            raise AuthError("authentication required")
        if perm_name not in user.permissions:
            raise ForbiddenError(
                f"permission required: {perm_name}",
                details={"required_permission": perm_name},
            )
        return user

    return _dep


def assert_tenant_match(row_tenant_id: uuid.UUID, user) -> None:
    """Cross-tenant guard. Use after loading a row scoped by tenant_id.

    Returns 404 (not 403) on mismatch — we don't want to leak existence
    of resources belonging to other tenants.
    """
    if user is None or row_tenant_id != user.tenant_id:
        raise NotFoundError("resource not found")


# ──────────────────────────────────────────────────────────────────────────
# Tenant resolution — transition-aware
# ──────────────────────────────────────────────────────────────────────────


async def get_current_tenant_id(
    request: Request,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> uuid.UUID:
    """Resolve the tenant for this request, with transition path:

    1. JWT (if ``get_current_user`` populated ``request.state.current_user``).
    2. ``X-Tenant-Id`` header — allowed only when ``env=='dev'`` AND
       ``multi_tenant_enabled``. Will be removed once Phase 2 is fully
       enforced.
    3. ``settings.default_tenant_id`` (single-tenant MVP fallback).
    """
    settings = get_settings()

    user = getattr(request.state, "current_user", None)
    if user is not None:
        return user.tenant_id

    if x_tenant_id is not None:
        if settings.env != "dev":
            raise ForbiddenError("X-Tenant-Id header only allowed in dev")
        try:
            return uuid.UUID(x_tenant_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid tenant id: {x_tenant_id!r}",
            ) from exc

    if settings.multi_tenant_enabled and settings.auth_enforcement == "enforce":
        raise AuthError("no tenant context")

    try:
        return uuid.UUID(settings.default_tenant_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid default tenant id: {settings.default_tenant_id!r}",
        ) from exc


# ──────────────────────────────────────────────────────────────────────────
# Convenience aliases for cleaner router signatures
# ──────────────────────────────────────────────────────────────────────────


DbSession = Annotated[AsyncSession, Depends(get_db)]
Messenger = Annotated[LineMessenger, Depends(get_line_messenger)]
TenantId = Annotated[uuid.UUID, Depends(get_current_tenant_id)]


__all__ = [
    "DbSession",
    "Messenger",
    "TenantId",
    "assert_tenant_match",
    "get_current_tenant_id",
    "get_current_user",
    "get_db",
    "get_line_channel_secret",
    "get_line_messenger",
    "require_any_role",
    "require_permission",
    "require_role",
]
