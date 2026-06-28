"""Pydantic schemas for /auth router — login, refresh, logout, me, change_password.

Conforms to specs/auth_rbac_system.md §Pydantic Schemas.

All inputs are ``ConfigDict(frozen=True)`` — NOT ``strict=True``, since
that would reject the JSON-encoded UUID strings we serialize tenant_id
as (matches CLAUDE.md guidance).

Passwords are wrapped in ``SecretStr`` so accidental logging produces
``SecretStr('**********')`` instead of the plaintext.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    refresh_token: SecretStr = Field(min_length=64, max_length=64)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    refresh_token: SecretStr = Field(min_length=64, max_length=64)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    old_password: SecretStr = Field(min_length=1, max_length=256)
    new_password: SecretStr = Field(min_length=8, max_length=256)

    @field_validator("new_password")
    @classmethod
    def _complexity(cls, v: SecretStr) -> SecretStr:
        plain = v.get_secret_value()
        has_letter = any(c.isalpha() for c in plain)
        has_digit = any(c.isdigit() for c in plain)
        if not (has_letter and has_digit):
            raise ValueError(
                "new_password must contain at least one letter and one digit"
            )
        return v


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class TokenPair(BaseModel):
    """Wire shape for the access+refresh token pair.

    ``expires_in`` is the access-token TTL in seconds (the refresh
    token's TTL isn't surfaced — clients shouldn't make decisions on it).
    """

    model_config = ConfigDict(from_attributes=True)
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class LoginResponse(TokenPair):
    """Same shape as TokenPair; alias for clarity in OpenAPI spec."""


class MeResponse(BaseModel):
    """``GET /auth/me`` — full identity surface for the logged-in user."""

    model_config = ConfigDict(from_attributes=True)
    employee_id: UUID
    tenant_id: UUID
    email: EmailStr
    display_name: str         # mirrors employees.full_name
    employee_role: str        # employees.role enum value
    functional_roles: list[str]
    permissions: list[str]
    last_login_at: datetime | None


__all__ = [
    "ChangePasswordRequest",
    "LoginRequest",
    "LoginResponse",
    "LogoutRequest",
    "MeResponse",
    "RefreshRequest",
    "TokenPair",
]
