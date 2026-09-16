"""Employee PIN hashing — stdlib only (PBKDF2-HMAC-SHA256), no new dependency.

Matches the project's no-runtime-deps ethos (same reason UUIDv7 is hand-rolled).
A PIN is short (4-6 digits) so it is inherently low-entropy — PBKDF2 with a
per-PIN random salt and a high iteration count raises the offline-guessing cost,
and login attempts should be rate-limited per store at the edge (a later add).

Stored format (single opaque column ``employees.pin_hash``)::

    pbkdf2_sha256$<iterations>$<salt_b64url>$<derived_b64url>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 100_000
_SALT_BYTES = 16


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


def hash_pin(pin: str) -> str:
    """Return an opaque, self-describing hash string for ``pin``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${_b64e(salt)}${_b64e(dk)}"


def verify_pin(pin: str, stored: str | None) -> bool:
    """Constant-time verify ``pin`` against a stored hash. False if unset/malformed."""
    if not stored:
        return False
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        salt = _b64d(salt_b64)
        expected = _b64d(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


__all__ = ["hash_pin", "verify_pin"]
