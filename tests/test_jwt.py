"""Unit tests for restaurant_api.security.jwt.

Pure-function tests over the JWT encode/decode + refresh-token helpers.
No DB, no FastAPI app. The deps layer is tested separately in
test_rbac_deps.py.
"""

from __future__ import annotations

import time
import uuid

import jwt as pyjwt
import pytest

from restaurant_api.config import get_settings
from restaurant_api.security.jwt import (
    JwtError,
    decode_access_token,
    encode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)


def _sample_claims() -> dict:
    return {
        "employee_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "employee_role": "manager",
        "roles": ["store_manager", "marketing"],
        "permissions": ["orders:create", "orders:read"],
    }


def test_encode_decode_roundtrip() -> None:
    claims = _sample_claims()
    token, jti, exp = encode_access_token(**claims)
    assert isinstance(token, str)
    assert len(jti) > 0
    assert exp > int(time.time())

    payload = decode_access_token(token)
    assert payload.sub == claims["employee_id"]
    assert payload.tenant_id == claims["tenant_id"]
    assert payload.employee_role == claims["employee_role"]
    assert payload.roles == claims["roles"]
    assert payload.permissions == claims["permissions"]
    assert payload.jti == jti
    assert payload.exp == exp


def test_decode_rejects_tampered_signature() -> None:
    token, _, _ = encode_access_token(**_sample_claims())
    # Flip the last char of the signature — pyjwt will fail signature check.
    bad = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(JwtError):
        decode_access_token(bad)


def test_decode_rejects_expired_token() -> None:
    """Issue with a negative TTL (manually-crafted) → expired immediately."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "employee_role": "staff",
        "roles": [],
        "permissions": [],
        "iat": now - 3600,
        "exp": now - 1,  # 1 second ago
        "jti": str(uuid.uuid4()),
    }
    expired = pyjwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    with pytest.raises(JwtError, match="expired"):
        decode_access_token(expired)


def test_decode_rejects_wrong_algorithm() -> None:
    """A token signed with HS512 must NOT validate under our HS256 setting."""
    settings = get_settings()
    payload = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "jti": str(uuid.uuid4()),
    }
    hs512_token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS512")
    with pytest.raises(JwtError):
        decode_access_token(hs512_token)


def test_decode_rejects_missing_required_claim() -> None:
    """A token without ``jti`` must fail the require check."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 60,
        # NO jti
    }
    token = pyjwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    with pytest.raises(JwtError):
        decode_access_token(token)


def test_decode_rejects_malformed_uuid() -> None:
    """sub that isn't a valid UUID → JwtError, not crash."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": "not-a-uuid",
        "tenant_id": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
    }
    token = pyjwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    with pytest.raises(JwtError, match="malformed"):
        decode_access_token(token)


def test_refresh_token_shape() -> None:
    raw, digest, exp = generate_refresh_token()
    assert len(raw) == 64  # hex = 32 bytes * 2
    assert all(c in "0123456789abcdef" for c in raw)
    assert len(digest) == 64
    assert digest == hash_refresh_token(raw)
    assert exp > int(time.time())


def test_refresh_tokens_unique_per_call() -> None:
    """100 generations all produce distinct raw tokens (entropy sanity)."""
    seen: set[str] = set()
    for _ in range(100):
        raw, _, _ = generate_refresh_token()
        assert raw not in seen
        seen.add(raw)


def test_jti_unique_per_encode() -> None:
    """Each access-token mint gets a fresh jti by default."""
    _, jti_1, _ = encode_access_token(**_sample_claims())
    _, jti_2, _ = encode_access_token(**_sample_claims())
    assert jti_1 != jti_2


def test_explicit_jti_passthrough() -> None:
    """Callers that want to control jti can override (used for testing
    revocation tracking)."""
    forced = str(uuid.uuid4())
    _, jti, _ = encode_access_token(jti=forced, **_sample_claims())
    assert jti == forced
