"""argon2id password hashing wrapper.

We pin to passlib's CryptContext so any future migration to scrypt /
bcrypt is a one-line scheme swap without touching callers. argon2id is
OWASP's 2024 recommendation; defaults follow the OWASP "memory ≥ 19 MB,
iterations ≥ 2" baseline.

Verification is constant-time at the algorithm level (argon2 itself).
For the higher-level timing-attack defence (don't reveal whether a user
exists), see ``auth_service.login`` which always calls ``verify_password``
even when the lookup misses.
"""

from __future__ import annotations

from passlib.context import CryptContext

# Single global context. CryptContext is thread-safe.
_PWD_CONTEXT = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    # argon2 defaults from passlib are already OWASP-compliant
    # (m=65536, t=3, p=4); pin explicitly so a passlib upgrade can't
    # silently weaken them.
    argon2__memory_cost=65_536,
    argon2__time_cost=3,
    argon2__parallelism=4,
)

# A pre-hashed dummy password used to keep response time constant when
# the email lookup misses — verify_password against this still spends
# the full argon2 work factor. Generated once at module import.
_DUMMY_HASH: str = _PWD_CONTEXT.hash("dummy-password-for-timing-attack-defence")


def hash_password(plain: str) -> str:
    """Return an argon2id hash of ``plain``. Hash is self-describing
    (algorithm + parameters embedded), so future verification doesn't
    need a separate "which algo" lookup.
    """
    return _PWD_CONTEXT.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify. Returns False on any mismatch including
    a malformed stored hash (we never raise; that would leak via
    behaviour-differs-on-bad-hash)."""
    try:
        return _PWD_CONTEXT.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def verify_dummy() -> None:
    """Spend one argon2id verify against the dummy hash.

    Called by ``auth_service.login`` when the email lookup misses, so
    failure response time matches success response time. Returns nothing
    because the result is intentionally discarded.
    """
    _PWD_CONTEXT.verify("any-input", _DUMMY_HASH)


__all__ = ["hash_password", "verify_dummy", "verify_password"]
