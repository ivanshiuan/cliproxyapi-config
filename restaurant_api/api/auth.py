"""Admin-console authentication — shared passcode → signed session cookie.

The 店長後台 (`/demo/admin.html`) and the management/counter endpoints behind
it are gated by a single shared passcode (``Settings.admin_passcode``). A
correct passcode mints a short-lived, HMAC-signed, ``httpOnly`` cookie; every
protected endpoint then depends on :func:`require_admin`, which verifies the
cookie's signature and expiry.

This is deliberately a *single shared credential* (Phase-1 店長 console), not
per-employee accounts — no DB migration, no password column. The signing key
(``Settings.session_secret``) and passcode MUST be overridden in production.

Token format (cookie value)::

    base64url(payload_json) "." base64url(hmac_sha256(payload_json, secret))

where ``payload_json = {"sub": "store-admin", "exp": <unix-seconds>}``.
Stateless: no server-side session store, so logout is best-effort (clears the
cookie); a stolen unexpired token stays valid until ``exp``. TTL is short.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from ..config import Settings, get_settings

COOKIE_NAME = "admin_session"
_SUBJECT = "store-admin"


class AdminPrincipal(BaseModel):
    """The authenticated admin identity attached to a request."""

    model_config = ConfigDict(frozen=True)
    subject: str = _SUBJECT


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    passcode: str


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


def _sign(payload: bytes, secret: str) -> str:
    return _b64e(hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest())


def make_session_token(settings: Settings, *, now: int | None = None) -> str:
    """Mint a signed session token valid for ``admin_session_ttl_seconds``."""
    issued = int(time.time()) if now is None else now
    payload = json.dumps(
        {"sub": _SUBJECT, "exp": issued + settings.admin_session_ttl_seconds},
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{_b64e(payload)}.{_sign(payload, settings.session_secret)}"


def verify_session_token(token: str, settings: Settings, *, now: int | None = None) -> bool:
    """Return True iff ``token`` is well-formed, correctly signed, and unexpired."""
    try:
        payload_b64, sig = token.split(".", 1)
        payload = _b64d(payload_b64)  # binascii.Error subclasses ValueError
    except (ValueError, TypeError):
        return False
    expected = _sign(payload, settings.session_secret)
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return False
    if data.get("sub") != _SUBJECT:
        return False
    current = int(time.time()) if now is None else now
    return isinstance(data.get("exp"), int) and data["exp"] > current


def require_admin(
    admin_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> AdminPrincipal:
    """Dependency: 401 unless a valid admin session cookie is present.

    Tests override this in the ``client`` fixture so existing router tests run
    authenticated by default; the auth-specific tests use a client that keeps
    this dependency live.
    """
    settings = get_settings()
    if not admin_session or not verify_session_token(admin_session, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin login required",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return AdminPrincipal()


Admin = Annotated[AdminPrincipal, Depends(require_admin)]

router = APIRouter(prefix="/admin", tags=["admin-auth"])


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.admin_session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.env == "prod",
        path="/",
    )


@router.post("/login", summary="店長後台登入 (共享通行碼 → session cookie)")
def login(payload: LoginRequest, response: Response) -> dict[str, bool]:
    """Verify the shared passcode in constant time; on success set the cookie."""
    settings = get_settings()
    if not hmac.compare_digest(payload.passcode, settings.admin_passcode):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="passcode 錯誤",
        )
    _set_cookie(response, make_session_token(settings), settings)
    return {"ok": True}


@router.post("/logout", summary="登出 (清除 session cookie)")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/session", summary="檢查目前登入狀態 (UI 載入時呼叫)")
def session(_: Admin) -> dict[str, bool]:
    """200 + ``{"authenticated": true}`` when logged in; 401 otherwise."""
    return {"authenticated": True}


__all__ = [
    "COOKIE_NAME",
    "Admin",
    "AdminPrincipal",
    "make_session_token",
    "require_admin",
    "router",
    "verify_session_token",
]
