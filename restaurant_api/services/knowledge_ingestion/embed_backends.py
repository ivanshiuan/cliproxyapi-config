"""Concrete embedding backends — 3 tiers, one factory.

Three implementations, all satisfying :class:`EmbeddingClient` from
:mod:`embed`:

* :class:`OpenAIEmbeddingClient` — 效率 tier, cheapest cloud option,
  ``text-embedding-3-small`` @ 1536 dims natively matches the schema.
* :class:`VoyageEmbeddingClient` — 高手 tier, better CJK retrieval.
  Voyage's Matryoshka embeddings are truncated from 2048 → 1536 so the
  vectors slot into the existing schema without a migration.
* :class:`LocalHashEmbeddingClient` — 免費 tier, dependency-free
  placeholder. Deterministic hash-based vectors so the pipeline runs
  end-to-end without any download / API key. Retrieval quality is
  intentionally poor (see docstring); the goal is to unblock the plumbing.
  Swap in ``sentence-transformers`` when you're ready to trade RAM for
  actual semantic search — see the module docstring at the bottom for the
  ~10 line diff.

Factory :func:`make_embedding_client_from_settings` reads
``RESTO_EMBED_BACKEND`` and returns the right client.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
from typing import Any

import httpx

from .embed import EmbeddingClient
from .errors import IngestionEmbedError

logger = logging.getLogger("restaurant_api.knowledge_ingestion.embed_backends")

# Schema-mandated dimension. All backends MUST return vectors of this size
# so they land in the shared ``embeddings.vector`` column without shims.
SCHEMA_VECTOR_DIM = 1536

# HTTP timeouts / retries — same shape as the LLM client so failure modes
# are consistent to reason about.
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_BACKOFF_S = (1.0, 4.0, 16.0)


async def _retry(coro_factory, *, name: str):
    """Await coro_factory() with fixed backoff. Coroutine factory pattern
    so the coroutine is fresh on each attempt (httpx requests can't be
    replayed)."""
    last: Exception | None = None
    for i in range(len(_BACKOFF_S) + 1):
        try:
            return await coro_factory()
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Only retry 5xx / 429 / plain transport errors. 4xx errors
            # (auth, model not found, etc.) are terminal.
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and status is not None
                and not (status == 429 or 500 <= status < 600)
            ):
                raise
            if i >= len(_BACKOFF_S):
                last = exc
                break
            logger.warning(
                "embed.retry",
                extra={"backend": name, "attempt": i + 1, "error": str(exc)},
            )
            await asyncio.sleep(_BACKOFF_S[i])
            last = exc
    assert last is not None
    raise IngestionEmbedError(
        f"{name} embedding failed after retries: {last}"
    ) from last


# ─── OpenAI ─────────────────────────────────────────────────────────────


class OpenAIEmbeddingClient:
    """OpenAI embeddings API. Default model is 1536-dim native.

    ``text-embedding-3-large`` supports the ``dimensions`` param so we
    request 1536 explicitly for consistency. ``text-embedding-3-small``
    is 1536 natively, so the param is a no-op there.
    """

    vector_dim: int = SCHEMA_VECTOR_DIM

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAIEmbeddingClient requires api_key")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def embed(
        self, texts: list[str], *, model: str
    ) -> list[list[float]]:
        # Honour a per-call override, but default to the constructed model.
        target = model if model.startswith("text-embedding-") else self._model
        payload: dict[str, Any] = {
            "model": target,
            "input": texts,
            "dimensions": SCHEMA_VECTOR_DIM,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async def _once() -> list[list[float]]:
            client = self._client or httpx.AsyncClient(timeout=_TIMEOUT)
            close_after = self._client is None
            try:
                resp = await client.post(
                    f"{self._base_url}/embeddings",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return [item["embedding"] for item in data["data"]]
            finally:
                if close_after:
                    await client.aclose()

        return await _retry(_once, name="openai")


# ─── Voyage ─────────────────────────────────────────────────────────────


class VoyageEmbeddingClient:
    """Voyage AI embeddings.

    ``voyage-3-large`` is Matryoshka-trained: its 2048-dim base embedding
    can be truncated to any smaller dim while preserving semantics. We
    request the 2048 base then slice to 1536 so vectors slot into the
    existing ``embeddings`` table without a schema migration.

    That truncation loses a small amount of quality (Voyage's own eval
    suggests <2% NDCG@10 drop between 2048 and 1024). For the BUFF OS
    corpus (a few hundred docs) it's imperceptible.
    """

    vector_dim: int = SCHEMA_VECTOR_DIM

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "voyage-3-large",
        base_url: str = "https://api.voyageai.com/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("VoyageEmbeddingClient requires api_key")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def embed(
        self, texts: list[str], *, model: str
    ) -> list[list[float]]:
        target = model if model.startswith("voyage-") else self._model
        payload: dict[str, Any] = {
            "model": target,
            "input": texts,
            # Voyage v1: request the base dim; we slice below.
            "output_dimension": 2048,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async def _once() -> list[list[float]]:
            client = self._client or httpx.AsyncClient(timeout=_TIMEOUT)
            close_after = self._client is None
            try:
                resp = await client.post(
                    f"{self._base_url}/embeddings",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                raw = [item["embedding"] for item in data["data"]]
                return [v[:SCHEMA_VECTOR_DIM] for v in raw]  # Matryoshka slice
            finally:
                if close_after:
                    await client.aclose()

        return await _retry(_once, name="voyage")


# ─── Local (免費 tier) ──────────────────────────────────────────────────


class LocalHashEmbeddingClient:
    """Zero-dependency deterministic embedder — the "免費" tier.

    Uses SHA-256 of the input text expanded across 1536 dims. Same text →
    same vector; different text → statistically different vector. Semantic
    quality is essentially character-N-gram at best — **do not treat this
    as a real retrieval baseline**. It exists so the whole pipeline can
    run end-to-end with zero external cost while you decide whether to
    pay for OpenAI or install a local model.

    When you're ready for real local semantics, replace the ``embed``
    body with sentence-transformers (see the __main__ note at bottom).
    """

    vector_dim: int = SCHEMA_VECTOR_DIM

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def embed(
        self, texts: list[str], *, model: str
    ) -> list[list[float]]:
        self.calls.append((list(texts), model))
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        # Grow a pseudo-random byte stream from the sha256 seed. Same
        # implementation shape as FakeEmbeddingClient (deliberately) so
        # the retrieval math is identical during local dev.
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        buf = bytearray()
        while len(buf) < SCHEMA_VECTOR_DIM * 4:
            seed = hashlib.sha256(seed).digest()
            buf.extend(seed)
        ints = struct.unpack_from(f"<{SCHEMA_VECTOR_DIM}I", bytes(buf))
        # Map to [-1, 1]. Deterministic, comparable, hilariously blind
        # to synonyms — see class docstring.
        return [(v / 2**32) * 2 - 1 for v in ints]


# ─── Factory ────────────────────────────────────────────────────────────


def make_embedding_client_from_settings() -> EmbeddingClient | None:
    """Build the embedding client selected by ``RESTO_EMBED_BACKEND``.

    Returns ``None`` if a cloud backend is selected but its key is empty
    — the scheduler wiring then logs-and-skips rather than crashing every
    job at 09:00.
    """
    from ...config import get_settings

    s = get_settings()
    backend = s.embed_backend
    if backend == "fake":
        # For local dev / smoke tests. Distinct from ``local`` so tests
        # can prove they're not accidentally hitting a real backend.
        from .embed import FakeEmbeddingClient

        return FakeEmbeddingClient(vector_dim=SCHEMA_VECTOR_DIM)
    if backend == "local":
        return LocalHashEmbeddingClient()
    if backend == "openai":
        if not s.openai_api_key:
            logger.warning(
                "embed.no_credentials",
                extra={"backend": "openai"},
            )
            return None
        return OpenAIEmbeddingClient(
            api_key=s.openai_api_key,
            model=s.embed_model,
        )
    if backend == "voyage":
        if not s.voyage_api_key:
            logger.warning(
                "embed.no_credentials",
                extra={"backend": "voyage"},
            )
            return None
        return VoyageEmbeddingClient(
            api_key=s.voyage_api_key,
            model=s.embed_model
            if s.embed_model.startswith("voyage-")
            else "voyage-3-large",
        )
    raise ValueError(f"unknown embed backend: {backend!r}")


__all__ = [
    "SCHEMA_VECTOR_DIM",
    "LocalHashEmbeddingClient",
    "OpenAIEmbeddingClient",
    "VoyageEmbeddingClient",
    "make_embedding_client_from_settings",
]


# ── For the day you upgrade LocalHashEmbeddingClient to real semantics ──
#
#     class LocalSbertEmbeddingClient:
#         vector_dim = SCHEMA_VECTOR_DIM
#         def __init__(self, *, model_name: str = "Alibaba-NLP/gte-Qwen2-1.5B-instruct"):
#             from sentence_transformers import SentenceTransformer  # ~2GB
#             self._m = SentenceTransformer(model_name)
#             assert self._m.get_sentence_embedding_dimension() == SCHEMA_VECTOR_DIM
#         async def embed(self, texts, *, model):
#             loop = asyncio.get_running_loop()
#             vectors = await loop.run_in_executor(
#                 None, lambda: self._m.encode(texts, normalize_embeddings=True).tolist()
#             )
#             return vectors
