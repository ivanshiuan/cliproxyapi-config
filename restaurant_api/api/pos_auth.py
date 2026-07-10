"""POS employee session token — HMAC-signed, stateless, shift-length TTL.

Distinct from ``api/auth.py`` (the single shared 店長後台 passcode): this token
identifies an *individual clocked-in employee* on the floor, carrying their id,
store, and role so sensitive-action gating (退菜/折扣) can decide without a DB
round-trip. Same signing primitives and key (``session_secret``) as the admin
token — reused, not re-implemented.

Token payload::

    {"sub": "pos", "eid": <employee_id>, "sid": <store_id>,
     "role": <role>, "exp": <unix-seconds>}
"""

from __future__ import annotations

import hmac
import json
import time
import uuid
from typing import Annotated

from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict

from ..config import Settings, get_settings
from ..models import EmployeeRole
from .auth import _b64d, _b64e, _sign

_SUBJECT = "pos"
# A POS login should survive a full shift; the boss can rotate PINs to revoke.
_POS_TTL_SECONDS = 12 * 3600


class PosPrincipal(BaseModel):
    """The authenticated floor employee attached to a POS request."""

    model_config = ConfigDict(frozen=True)
    employee_id: uuid.UUID
    store_id: uuid.UUID
    role: EmployeeRole


def make_pos_token(principal: PosPrincipal, settings: Settings, *, now: int | None = None) -> str:
    issued = int(time.time()) if now is None else now
    payload = json.dumps(
        {
            "sub": _SUBJECT,
            "eid": str(principal.employee_id),
            "sid": str(principal.store_id),
            "role": principal.role.value,
            "exp": issued + _POS_TTL_SECONDS,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{_b64e(payload)}.{_sign(payload, settings.session_secret)}"


def verify_pos_token(token: str, settings: Settings, *, now: int | None = None) -> PosPrincipal | None:
    """Return the principal iff the token is well-formed, signed, and unexpired."""
    try:
        payload_b64, sig = token.split(".", 1)
        payload = _b64d(payload_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(sig, _sign(payload, settings.session_secret)):
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if data.get("sub") != _SUBJECT:
        return None
    current = int(time.time()) if now is None else now
    if not (isinstance(data.get("exp"), int) and data["exp"] > current):
        return None
    try:
        return PosPrincipal(
            employee_id=uuid.UUID(data["eid"]),
            store_id=uuid.UUID(data["sid"]),
            role=EmployeeRole(data["role"]),
        )
    except (ValueError, KeyError):
        return None


def get_pos_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> PosPrincipal | None:
    """Optional POS identity from ``Authorization: Bearer <token>``.

    Returns None when absent/invalid — endpoints decide whether that's fatal
    (e.g. /me requires it) or merely means "no self-permission, fall back to a
    manager override" (退菜/折扣).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return verify_pos_token(authorization[7:].strip(), get_settings())


PosActor = Annotated["PosPrincipal | None", Depends(get_pos_principal)]


__all__ = [
    "PosActor",
    "PosPrincipal",
    "get_pos_principal",
    "make_pos_token",
    "verify_pos_token",
]
