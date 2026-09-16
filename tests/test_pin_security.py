"""Unit tests for stdlib PIN hashing (services/pin_security)."""

from __future__ import annotations

from restaurant_api.services.pin_security import hash_pin, verify_pin


def test_hash_verify_round_trip() -> None:
    h = hash_pin("1234")
    assert verify_pin("1234", h) is True
    assert verify_pin("9999", h) is False


def test_hash_is_salted_not_plaintext() -> None:
    h1 = hash_pin("4242")
    h2 = hash_pin("4242")
    assert "4242" not in h1          # never stores the raw PIN
    assert h1 != h2                  # per-hash random salt
    assert verify_pin("4242", h1) and verify_pin("4242", h2)


def test_verify_handles_missing_or_malformed() -> None:
    assert verify_pin("1234", None) is False
    assert verify_pin("1234", "") is False
    assert verify_pin("1234", "not-a-valid-hash") is False
    assert verify_pin("1234", "pbkdf2_sha256$bad$parts") is False
