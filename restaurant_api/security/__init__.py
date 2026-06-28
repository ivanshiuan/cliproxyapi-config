"""Auth security primitives — pure functions over passwords and JWTs.

These modules deliberately have **no DB access** and **no FastAPI imports**.
The auth service composes them; the deps layer composes the service. This
keeps each layer testable in isolation.
"""

from __future__ import annotations
