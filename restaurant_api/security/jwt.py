"""JWT encode/decode for the Phase 2 auth system.

Pure functions over the JWT bytes — no DB, no request context. Caller
(``auth_service`` for issuance, deps layer for verification) supplies
the payload fields. We do NOT bake business policy here; this module
just enforces the JWT-level invariants (signed, not expired, required
claims present).

Algorithm: HS256 (single shared secret, settings-driven). RS256 is a
future swap when we move to a public-key distribution model.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt as pyjwt
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidTokenError,
    PyJWTError,
)

from ..config import get_settings


@dataclass(frozen=True, slots=True)
class JwtPayload:
    """Decoded + validated access-token payload.

    Trust this only after passing through ``decode_access_token``; the
    raw dict from pyjwt may have arbitrary keys and isn't a contract.
    """

    sub: uuid.UUID           # employee_id
    tenant_id: uuid.UUID
    employee_role: str
    roles: list[str]
    permissions: list[str]
    jti: str
    iat: int
    exp: int


class JwtError(Exception):
    """Anything wrong with a JWT — wraps PyJWTError + our own invariant fails.

    The deps layer catches this and converts to AuthError(401). We keep
    it as a plain exception (not DomainError) so this module stays free
    of FastAPI imports.
    """


def encode_access_token(
    *,
    employee_id: uuid.UUID,
    tenant_id: uuid.UUID,
    employee_role: str,
    roles: list[str],
    permissions: list[str],
    jti: str | None = None,
) -> tuple[str, str, int]:
    """Sign a new access token.

    Returns ``(token_string, jti, expires_at_unix)``. The ``jti`` is
    surfaced so the caller can persist it if revocation tracking is ever
    added (currently we only revoke refresh tokens).
    """
    settings = get_settings()
    now = int(time.time())
    exp = now + settings.jwt_access_ttl_seconds
    jti_str = jti or str(uuid.uuid4())
    payload: dict[str, Any] = {
        "sub": str(employee_id),
        "tenant_id": str(tenant_id),
        "employee_role": employee_role,
        "roles": roles,
        "permissions": permissions,
        "iat": now,
        "exp": exp,
        "jti": jti_str,
    }
    token = pyjwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return token, jti_str, exp


def decode_access_token(token: str) -> JwtPayload:
    """Verify signature + expiry + required claims; return typed payload.

    Raises ``JwtError`` (NOT pyjwt's specific exceptions, NOT FastAPI's
    HTTPException) on any failure so callers have a single seam to catch.
    """
    settings = get_settings()
    try:
        raw: dict[str, Any] = pyjwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "tenant_id", "exp", "iat", "jti"]},
        )
    except ExpiredSignatureError as exc:
        raise JwtError("token expired") from exc
    except InvalidTokenError as exc:
        raise JwtError(f"invalid token: {exc}") from exc
    except PyJWTError as exc:  # defensive — pyjwt may raise the base class
        raise JwtError(f"jwt error: {exc}") from exc

    try:
        return JwtPayload(
            sub=uuid.UUID(raw["sub"]),
            tenant_id=uuid.UUID(raw["tenant_id"]),
            employee_role=str(raw.get("employee_role", "")),
            roles=list(raw.get("roles", [])),
            permissions=list(raw.get("permissions", [])),
            jti=str(raw["jti"]),
            iat=int(raw["iat"]),
            exp=int(raw["exp"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise JwtError(f"malformed claims: {exc}") from exc


# ---------------------------------------------------------------------------
# Refresh tokens — opaque random bytes, NOT JWTs.
# Stored as sha256(hex) in DB; the raw hex only appears in the response body
# once. We keep these helpers here because they share the same "auth secret"
# blast radius as JWTs (compromise either → reset both).
# ---------------------------------------------------------------------------


def generate_refresh_token() -> tuple[str, str, int]:
    """Return ``(raw_token, sha256_hex, expires_at_unix)``.

    raw_token: 64-char hex string (32 bytes urandom). Send to client.
    sha256_hex: 64-char hex string. Store in DB. Compare on refresh.
    """
    import hashlib

    raw = secrets.token_hex(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    settings = get_settings()
    expires_at = int(time.time()) + settings.jwt_refresh_ttl_seconds
    return raw, digest, expires_at


def hash_refresh_token(raw: str) -> str:
    """sha256 hex of a raw refresh token — used both to look up an
    incoming token and to compare for reuse detection."""
    import hashlib

    return hashlib.sha256(raw.encode()).hexdigest()


__all__ = [
    "JwtError",
    "JwtPayload",
    "decode_access_token",
    "encode_access_token",
    "generate_refresh_token",
    "hash_refresh_token",
]
