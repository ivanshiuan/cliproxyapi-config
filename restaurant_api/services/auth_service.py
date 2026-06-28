"""Auth service — login, refresh, logout, change_password orchestrations.

Pure async functions over a session; no FastAPI imports. Router code
catches the domain exceptions raised here and converts to HTTP.

Security invariants (see specs/auth_rbac_system.md):
- Login response time is constant regardless of whether the user exists
  (``verify_dummy()`` on miss).
- Refresh tokens rotate every use; reuse of a revoked token revokes
  the entire chain (likely compromise).
- Password change revokes all the user's refresh tokens (force re-login
  on all devices).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Employee, RefreshToken, UserCredential
from ..security.jwt import (
    encode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from ..security.passwords import (
    hash_password,
    verify_dummy,
    verify_password,
)
from . import audit_service
from .rbac_service import CurrentUser, load_user_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome types — services return data, routers convert to HTTP
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenPairResult:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthServiceError(Exception):
    """Base — never raised directly; subclass per failure mode."""


class InvalidCredentials(AuthServiceError):
    """Wrong password OR unknown email (caller cannot distinguish — by design)."""


class AccountDisabled(AuthServiceError):
    """is_active = false on user_credentials."""


class AccountLocked(AuthServiceError):
    """failed_login_count threshold hit; locked_until > now()."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("account locked")
        self.retry_after_seconds = retry_after_seconds


class InvalidRefreshToken(AuthServiceError):
    """Refresh token not found, expired, or already revoked."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _issue_token_pair(
    session: AsyncSession,
    *,
    user: CurrentUser,
    created_from_ip: str | None,
    user_agent: str | None,
) -> TokenPairResult:
    """Encode access JWT + mint + persist a new refresh token."""
    settings = get_settings()

    access_token, _jti, _exp = encode_access_token(
        employee_id=user.employee_id,
        tenant_id=user.tenant_id,
        employee_role=user.employee_role,
        roles=sorted(user.functional_roles),
        permissions=sorted(user.permissions),
    )

    raw_refresh, refresh_hash, refresh_exp = generate_refresh_token()
    session.add(
        RefreshToken(
            employee_id=user.employee_id,
            token_hash=refresh_hash,
            expires_at=datetime.fromtimestamp(refresh_exp, tz=UTC),
            created_from_ip=created_from_ip,
            user_agent=(user_agent or "")[:500] or None,
        )
    )
    await session.flush()

    return TokenPairResult(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.jwt_access_ttl_seconds,
    )


# ---------------------------------------------------------------------------
# Public service methods
# ---------------------------------------------------------------------------


async def login(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    created_from_ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPairResult:
    """Authenticate + issue token pair.

    Raises: InvalidCredentials | AccountDisabled | AccountLocked.
    Side effects: updates last_login_at on success; increments
    failed_login_count and possibly sets locked_until on failure.
    """
    settings = get_settings()
    normalized_email = email.strip().lower()

    cred = (
        await session.execute(
            select(UserCredential).where(
                UserCredential.email == normalized_email
            )
        )
    ).scalar_one_or_none()

    # Timing-attack defence — always spend one argon2 verify.
    if cred is None:
        verify_dummy()
        raise InvalidCredentials

    if not cred.is_active:
        # Still spend one verify so disabled-account response time matches
        # the not-found path.
        verify_dummy()
        raise AccountDisabled

    now = datetime.now(UTC)
    if cred.locked_until is not None and cred.locked_until > now:
        retry_after = int((cred.locked_until - now).total_seconds()) + 1
        raise AccountLocked(retry_after_seconds=retry_after)

    # Lockout has expired → reset counters before this attempt.
    if cred.locked_until is not None and cred.locked_until <= now:
        cred.failed_login_count = 0
        cred.locked_until = None

    if not verify_password(password, cred.password_hash):
        cred.failed_login_count += 1
        if cred.failed_login_count >= settings.auth_max_failed_logins:
            cred.locked_until = now + timedelta(
                seconds=settings.auth_lockout_seconds
            )
        await session.flush()
        raise InvalidCredentials

    # Success path.
    cred.failed_login_count = 0
    cred.locked_until = None
    cred.last_login_at = now

    user = await load_user_context(session, cred.employee_id)
    if user is None:
        # Should not happen — UserCredential.employee_id FK with CASCADE,
        # and Employee soft-delete would not satisfy "logged in" semantics.
        logger.error(
            "login.user_context.missing",
            extra={"employee_id": str(cred.employee_id)},
        )
        raise InvalidCredentials

    pair = await _issue_token_pair(
        session,
        user=user,
        created_from_ip=created_from_ip,
        user_agent=user_agent,
    )
    logger.info(
        "auth.login.success",
        extra={
            "employee_id": str(cred.employee_id),
            "tenant_id": str(cred.employee.tenant_id) if cred.employee else None,
        },
    )
    return pair


async def refresh(
    session: AsyncSession,
    *,
    refresh_token: str,
    created_from_ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPairResult:
    """Rotate refresh token + issue new access token.

    Detects reuse: if the supplied token is found but already revoked,
    revoke ALL the user's active refresh tokens (likely compromise).
    """
    digest = hash_refresh_token(refresh_token)
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == digest)
        )
    ).scalar_one_or_none()

    if row is None:
        raise InvalidRefreshToken

    now = datetime.now(UTC)
    if row.expires_at < now:
        raise InvalidRefreshToken

    if row.revoked_at is not None:
        # Reuse detected — torch all active sessions for this employee.
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.employee_id == row.employee_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await session.flush()
        logger.warning(
            "auth.refresh.reuse_detected",
            extra={"employee_id": str(row.employee_id)},
        )
        raise InvalidRefreshToken

    # Happy path — rotate.
    row.revoked_at = now
    user = await load_user_context(session, row.employee_id)
    if user is None:
        raise InvalidRefreshToken

    return await _issue_token_pair(
        session,
        user=user,
        created_from_ip=created_from_ip,
        user_agent=user_agent,
    )


async def logout(
    session: AsyncSession,
    *,
    refresh_token: str,
) -> None:
    """Revoke the supplied refresh token.

    Idempotent — if the token isn't found we return silently rather than
    leak that fact to the caller.
    """
    digest = hash_refresh_token(refresh_token)
    row = (
        await session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == digest,
                RefreshToken.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    row.revoked_at = datetime.now(UTC)
    await session.flush()


async def change_password(
    session: AsyncSession,
    *,
    employee_id: uuid.UUID,
    old_password: str,
    new_password: str,
) -> None:
    """Verify old, hash new, revoke all refresh tokens, audit.

    Raises InvalidCredentials if old_password is wrong. Increments
    failed_login_count (shares lockout state with login).
    """
    cred = (
        await session.execute(
            select(UserCredential).where(
                UserCredential.employee_id == employee_id
            )
        )
    ).scalar_one_or_none()
    if cred is None:
        raise InvalidCredentials

    if not verify_password(old_password, cred.password_hash):
        cred.failed_login_count += 1
        await session.flush()
        raise InvalidCredentials

    cred.password_hash = hash_password(new_password)
    cred.failed_login_count = 0
    cred.locked_until = None
    now = datetime.now(UTC)

    # Force re-login on all devices.
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.employee_id == employee_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await session.flush()

    # audit needs tenant_id — look it up from the employee row.
    emp = (
        await session.execute(
            select(Employee.tenant_id).where(Employee.id == employee_id)
        )
    ).scalar_one()
    await audit_service.audit(
        session,
        action="auth.password_changed",
        tenant_id=emp,
        target=("user_credentials", cred.id),
        actor_id=employee_id,
        after=None,
        reason="self-service password change",
    )


__all__ = [
    "AccountDisabled",
    "AccountLocked",
    "AuthServiceError",
    "InvalidCredentials",
    "InvalidRefreshToken",
    "TokenPairResult",
    "change_password",
    "login",
    "logout",
    "refresh",
]
