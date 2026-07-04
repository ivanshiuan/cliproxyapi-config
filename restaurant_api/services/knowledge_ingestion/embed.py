"""Embedding client Protocol + a deterministic Fake for tests.

The real HTTP-backed clients (Voyage / OpenAI) live in a follow-up spec
(so this module has ZERO external network dependencies and can be
unit-tested in isolation).

Cost table is source-of-truth: any change requires a matching update in
``specs/knowledge_ingestion.md`` §5 and its AC-12 test.
"""

from __future__ import annotations

import hashlib
import struct
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

# Per-million-token cost in USD. Values match spec §5.
COST_PER_MILLION_TOKENS: dict[str, Decimal] = {
    "voyage-3-large": Decimal("0.18"),
    "voyage-3": Decimal("0.06"),
    "text-embedding-3-large": Decimal("0.13"),
    "text-embedding-3-small": Decimal("0.02"),
}


class EmbeddingClient(Protocol):
    """The shape every embedding backend must implement.

    ``embed`` returns one vector per input text, in the same order. All
    vectors have the same dimensionality equal to ``vector_dim``.
    """

    vector_dim: int

    async def embed(
        self, texts: list[str], *, model: str
    ) -> list[list[float]]:  # pragma: no cover — Protocol
        ...


def estimate_cost_usd(*, tokens: int, model: str) -> Decimal:
    """Return the USD cost quantised to 6dp. Zero for unknown models
    is deliberately NOT permitted — spec §5 pins the rate table."""
    rate = COST_PER_MILLION_TOKENS[model]
    cost = (Decimal(tokens) * rate) / Decimal("1000000")
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


class FakeEmbeddingClient:
    """Deterministic in-memory embedding for tests.

    Each vector is derived from a SHA-256 of the input text, split into
    ``vector_dim`` float32 lanes normalised to [-1, 1]. Same text → same
    vector, but two different texts effectively always differ.
    """

    def __init__(self, *, vector_dim: int = 1536) -> None:
        self.vector_dim = vector_dim
        self.calls: list[tuple[list[str], str]] = []

    async def embed(
        self, texts: list[str], *, model: str
    ) -> list[list[float]]:
        self.calls.append((list(texts), model))
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        # Grow a pseudo-random byte stream from the sha256 seed.
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        buf = bytearray()
        while len(buf) < self.vector_dim * 4:
            seed = hashlib.sha256(seed).digest()
            buf.extend(seed)
        # Unpack as unsigned int32s → map to [-1, 1] deterministically.
        ints = struct.unpack_from(f"<{self.vector_dim}I", bytes(buf))
        return [(v / 2**32) * 2 - 1 for v in ints]


__all__ = [
    "COST_PER_MILLION_TOKENS",
    "EmbeddingClient",
    "FakeEmbeddingClient",
    "estimate_cost_usd",
]
