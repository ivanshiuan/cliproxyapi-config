"""FastAPI router for ``/auth`` — per-employee JWT authentication.

NOT the Phase-1 admin-console passcode auth (that lives in
``restaurant_api/api/auth.py`` at prefix ``/admin``); this is the Phase 2
per-employee login flow described in ``specs/auth_rbac_system.md``.

The router parses HTTP, the service does the work, the deps layer
provides the request context. Five endpoints:

    POST /auth/login           email + password → access + refresh
    POST /auth/refresh         rotate refresh, mint new access
    POST /auth/logout          revoke refresh (idempotent)
    GET  /auth/me              current user info + roles + permissions
    POST /auth/change_password old + new → revoke all sessions
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response, status

from ..api.deps import DbSession, get_current_user
from ..api.errors import AuthError, ForbiddenError, LockedError
from ..config import get_settings
from ..schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
)
from ..services import auth_service
from ..services.rbac_service import CurrentUser, load_user_context

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP — prefer X-Forwarded-For (Cloudflare / reverse
    proxy adds it), fall back to direct connection.
    """
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.client.host if request.client else None


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate with email + password, return access + refresh tokens.",
)
async def login_endpoint(
    payload: LoginRequest,
    session: DbSession,
    request: Request,
    response: Response,
    user_agent: str | None = Header(default=None, alias="User-Agent"),
) -> LoginResponse:
    try:
        pair = await auth_service.login(
            session,
            email=payload.email,
            password=payload.password.get_secret_value(),
            created_from_ip=_client_ip(request),
            user_agent=user_agent,
        )
    except auth_service.AccountLocked as exc:
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        raise LockedError(
            "account locked due to repeated failed login attempts",
            details={"retry_after_seconds": exc.retry_after_seconds},
        ) from exc
    except auth_service.AccountDisabled as exc:
        raise ForbiddenError("account disabled") from exc
    except auth_service.InvalidCredentials as exc:
        raise AuthError("invalid credentials") from exc

    return LoginResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type="Bearer",
        expires_in=pair.expires_in,
    )


@router.post(
    "/refresh",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate refresh token; mint new access + refresh pair.",
)
async def refresh_endpoint(
    payload: RefreshRequest,
    session: DbSession,
    request: Request,
    user_agent: str | None = Header(default=None, alias="User-Agent"),
) -> LoginResponse:
    try:
        pair = await auth_service.refresh(
            session,
            refresh_token=payload.refresh_token.get_secret_value(),
            created_from_ip=_client_ip(request),
            user_agent=user_agent,
        )
    except auth_service.InvalidRefreshToken as exc:
        raise AuthError("invalid refresh token") from exc

    return LoginResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type="Bearer",
        expires_in=pair.expires_in,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the supplied refresh token (idempotent).",
)
async def logout_endpoint(
    payload: LogoutRequest,
    session: DbSession,
    user: CurrentUser | None = Depends(get_current_user),
) -> Response:
    # Require auth even though we don't use the user payload — the
    # logout button should require a live session, so a leaked refresh
    # token alone can't be used to "logout" a user.
    if user is None and get_settings().auth_enforcement == "enforce":
        raise AuthError("authentication required")
    await auth_service.logout(
        session, refresh_token=payload.refresh_token.get_secret_value()
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=MeResponse,
    status_code=status.HTTP_200_OK,
    summary="Return the authenticated user's identity, roles, and permissions.",
)
async def me_endpoint(
    session: DbSession,
    user: CurrentUser | None = Depends(get_current_user),
) -> MeResponse:
    if user is None:
        raise AuthError("authentication required")
    # Re-hydrate from DB so permissions / roles reflect any grants since
    # the JWT was issued.
    fresh = await load_user_context(session, user.employee_id, jti=user.jti)
    if fresh is None:
        raise AuthError("user no longer exists")

    # `last_login_at` lives on user_credentials — fetch via the relationship.
    last_login = None
    # rbac_service intentionally keeps load_user_context light; we look up
    # last_login_at on demand here rather than always.
    from sqlalchemy import select  # local — avoid top-level cost on hot paths

    from ..models import UserCredential
    cred = (
        await session.execute(
            select(UserCredential).where(
                UserCredential.employee_id == fresh.employee_id
            )
        )
    ).scalar_one_or_none()
    if cred is not None:
        last_login = cred.last_login_at

    return MeResponse(
        employee_id=fresh.employee_id,
        tenant_id=fresh.tenant_id,
        email=fresh.email or (cred.email if cred else ""),
        display_name=fresh.display_name,
        employee_role=fresh.employee_role,
        functional_roles=sorted(fresh.functional_roles),
        permissions=sorted(fresh.permissions),
        last_login_at=last_login,
    )


@router.post(
    "/change_password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change own password — revokes all active refresh tokens.",
)
async def change_password_endpoint(
    payload: ChangePasswordRequest,
    session: DbSession,
    user: CurrentUser | None = Depends(get_current_user),
) -> Response:
    if user is None:
        raise AuthError("authentication required")
    try:
        await auth_service.change_password(
            session,
            employee_id=user.employee_id,
            old_password=payload.old_password.get_secret_value(),
            new_password=payload.new_password.get_secret_value(),
        )
    except auth_service.InvalidCredentials as exc:
        raise AuthError("invalid credentials") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
