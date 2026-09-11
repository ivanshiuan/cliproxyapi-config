"""Knowledge ingestion service — file / url / drive / inline → DB.

Public entry point:

    from restaurant_api.services.knowledge_ingestion import ingest_source

    result = await ingest_source(
        source=IngestionSource(
            tenant_id=tenant.id,
            scope="funding",
            inline_bytes_b64=base64.b64encode(b"# BP\n\n投資人回覆…").decode(),
            tags=["BP", "2026Q2"],
        ),
        session=session,
        embedding_client=embedding_client,
        extractor=extractor,
    )

See ``specs/knowledge_ingestion.md`` for the full spec and 17 acceptance
criteria. Implementation lives in the sibling modules; this package
re-exports the public surface.
"""

from __future__ import annotations

from .chunk import Chunk, chunk_markdown
from .embed import (
    COST_PER_MILLION_TOKENS,
    EmbeddingClient,
    FakeEmbeddingClient,
    estimate_cost_usd,
)
from .errors import (
    IngestionDeniedError,
    IngestionEmbedError,
    IngestionError,
    IngestionExtractError,
)
from .extract import Extractor, FakeExtractor
from .pipeline import ingest_source
from .redact import DENY_LIST, RedactionHit, redact
from .schemas import IngestionResult, IngestionSource

__all__ = [
    "COST_PER_MILLION_TOKENS",
    "DENY_LIST",
    "Chunk",
    "EmbeddingClient",
    "Extractor",
    "FakeEmbeddingClient",
    "FakeExtractor",
    "IngestionDeniedError",
    "IngestionEmbedError",
    "IngestionError",
    "IngestionExtractError",
    "IngestionResult",
    "IngestionSource",
    "RedactionHit",
    "chunk_markdown",
    "estimate_cost_usd",
    "ingest_source",
    "redact",
]
