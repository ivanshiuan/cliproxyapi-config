"""Unit tests for restaurant_api.security.passwords.

Pure-function tests, no DB. Validates argon2id behaviour and the
timing-attack defence helper.
"""

from __future__ import annotations

import time

import pytest

from restaurant_api.security.passwords import (
    hash_password,
    verify_dummy,
    verify_password,
)


def test_hash_and_verify_roundtrip() -> None:
    """A hashed password verifies; the same input hashed twice yields
    different output (salt). Both verify."""
    h1 = hash_password("correct horse battery staple")
    h2 = hash_password("correct horse battery staple")
    assert h1 != h2  # salts differ
    assert verify_password("correct horse battery staple", h1)
    assert verify_password("correct horse battery staple", h2)


def test_wrong_password_rejected() -> None:
    h = hash_password("right")
    assert not verify_password("wrong", h)
    assert not verify_password("Right", h)  # case-sensitive
    assert not verify_password("", h)


def test_verify_malformed_hash_returns_false_not_raises() -> None:
    """A malformed stored hash must return False, not raise — raising
    would create a side-channel telling attackers ``hash is broken``."""
    assert not verify_password("anything", "not-a-valid-argon2-hash")
    assert not verify_password("anything", "")


def test_hash_self_describing() -> None:
    """Argon2id hash includes algorithm + params prefix so verify
    doesn't need a separate look-up table."""
    h = hash_password("x")
    assert h.startswith("$argon2id$"), f"expected argon2id prefix, got {h[:20]!r}"


def test_verify_dummy_does_not_raise() -> None:
    """verify_dummy must be callable any number of times without raising —
    it's the timing-attack defence and is invoked on every failed lookup."""
    for _ in range(3):
        verify_dummy()


def test_verify_dummy_consumes_real_work() -> None:
    """Sanity check that verify_dummy actually spends argon2 work — if
    it returned instantly, the timing-attack defence wouldn't help."""
    start = time.perf_counter()
    verify_dummy()
    elapsed = time.perf_counter() - start
    # OWASP baseline argon2id (m=65536, t=3, p=4) should take ≥ 5 ms on
    # a modern x86 even with parallelism; well within a 100ms ceiling.
    assert elapsed > 0.005, f"verify_dummy too fast ({elapsed*1000:.1f}ms)"


@pytest.mark.parametrize(
    "pw",
    [
        "a",                                  # single char
        "x" * 256,                            # at max length
        "中文密碼",                            # unicode
        "with spaces and !@#$%^&*()",         # special chars
        "🔥emoji🔥",                          # emoji
    ],
)
def test_unicode_and_edge_lengths(pw: str) -> None:
    h = hash_password(pw)
    assert verify_password(pw, h)
    assert not verify_password(pw + "x", h)
