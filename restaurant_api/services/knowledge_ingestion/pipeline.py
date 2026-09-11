"""Ingestion pipeline — the single canonical write path.

Contract (see ``specs/knowledge_ingestion.md`` §8, §9):

* One ``ingest_source`` call = one DB transaction.
* Embeddings for all chunks are collected in-memory BEFORE any DB
  INSERT, so an embed failure never leaves the DB half-populated.
* Duplicate detection (sha256 idempotency) short-circuits BEFORE
  extracting / embedding — zero LLM cost on re-ingest.
* Sensitive-doc detection is a side effect of redaction; the document
  is written either way so the audit trail is complete.
* Every successful ingestion writes one ``audit_log`` row via the
  ``audit_service.audit()`` API.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Embedding, KnowledgeChunk, KnowledgeDocument, KnowledgeScope
from ..audit_service import audit
from .chunk import Chunk, chunk_markdown
from .embed import EmbeddingClient, estimate_cost_usd
from .errors import IngestionDeniedError, IngestionEmbedError
from .extract import Extractor
from .redact import redact
from .schemas import IngestionResult, IngestionSource

logger = logging.getLogger("restaurant_api.knowledge_ingestion")

MAX_INLINE_BYTES = 200 * 1024 * 1024  # 200 MB
_SCOPE_MAP: dict[str, KnowledgeScope] = {s.value: s for s in KnowledgeScope}


async def ingest_source(
    source: IngestionSource,
    *,
    session: AsyncSession,
    embedding_client: EmbeddingClient,
    extractor: Extractor,
    now: datetime | None = None,
) -> IngestionResult:
    """One-shot ingestion. See module docstring for contract."""
    now_ts = now if now is not None else datetime.now(UTC)

    # 1. Resolve source → bytes
    raw = await _load_source_bytes(source)
    if len(raw) > MAX_INLINE_BYTES:
        raise IngestionDeniedError(
            f"source exceeds {MAX_INLINE_BYTES} bytes cap"
        )
    sha = hashlib.sha256(raw).hexdigest()

    # 2. Idempotency: has this (tenant, sha) been ingested already?
    existing_stmt = select(KnowledgeDocument).where(
        KnowledgeDocument.tenant_id == source.tenant_id,
        KnowledgeDocument.sha256 == sha,
        KnowledgeDocument.deleted_at.is_(None),
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "knowledge.ingest.duplicate",
            extra={
                "document_id": str(existing.id),
                "tenant_id": str(source.tenant_id),
            },
        )
        return IngestionResult(
            document_id=existing.id,
            is_duplicate=True,
            is_sensitive=existing.is_sensitive,
            chunk_count=0,
            embedding_count=0,
            redactions=[],
            tokens_embedded=0,
            cost_usd=Decimal("0.000000"),
            extract_ms=0,
            embed_ms=0,
            write_ms=0,
        )

    # 3. Extract → markdown
    t0 = time.monotonic()
    markdown = await extractor.extract(
        source_bytes=raw, mime_type=_mime_for_source(source)
    )
    extract_ms = int((time.monotonic() - t0) * 1000)

    # 4. Redact
    redacted, hits = redact(markdown)
    is_sensitive = source.explicit_sensitive or bool(hits)

    # 5. Chunk
    chunks: list[Chunk] = chunk_markdown(
        redacted,
        max_tokens=source.max_tokens_per_chunk,
        overlap_tokens=source.chunk_overlap_tokens,
    )

    tokens_embedded = sum(c.token_count for c in chunks)

    # dry_run: don't touch the DB, don't call the embed API.
    if source.dry_run:
        return IngestionResult(
            document_id=_dummy_uuid(),
            is_duplicate=False,
            is_sensitive=is_sensitive,
            chunk_count=len(chunks),
            embedding_count=0,
            redactions=hits,
            tokens_embedded=tokens_embedded,
            cost_usd=Decimal("0.000000"),
            extract_ms=extract_ms,
            embed_ms=0,
            write_ms=0,
        )

    # 6. Embed all chunks in memory before writing anything.
    vectors: list[list[float]] = []
    t1 = time.monotonic()
    if chunks:
        try:
            vectors = await embedding_client.embed(
                [c.text for c in chunks], model=source.embed_model
            )
        except IngestionEmbedError:
            raise
        except Exception as exc:
            raise IngestionEmbedError(
                f"embedding provider failed: {type(exc).__name__}: {exc}"
            ) from exc
        if len(vectors) != len(chunks):
            raise IngestionEmbedError(
                f"embedding provider returned {len(vectors)} vectors "
                f"for {len(chunks)} chunks"
            )
        for v in vectors:
            if len(v) != embedding_client.vector_dim:
                raise IngestionEmbedError(
                    f"vector dim {len(v)} != expected {embedding_client.vector_dim}"
                )
    embed_ms = int((time.monotonic() - t1) * 1000)

    # 7. Write DB in a single transaction (caller commits).
    t2 = time.monotonic()
    scope_enum = _SCOPE_MAP[source.scope]
    title = source.title or _title_from(source, raw)
    doc = KnowledgeDocument(
        tenant_id=source.tenant_id,
        scope=scope_enum,
        source_type=_source_type_for(source),
        source_uri=_source_uri_for(source),
        title=title,
        sha256=sha,
        byte_size=len(raw),
        mime_type=_mime_for_source(source),
        language=None,  # can be filled by adapters later
        ingested_at=now_ts,
        source_modified_at=source.source_modified_at,
        meta={},
        is_sensitive=is_sensitive,
        tags=list(source.tags),
    )
    session.add(doc)
    await session.flush()

    chunks_written = 0
    embeddings_written = 0
    for c, vec in zip(chunks, vectors, strict=True):
        chunk_row = KnowledgeChunk(
            tenant_id=source.tenant_id,
            document_id=doc.id,
            chunk_idx=c.chunk_idx,
            text=c.text,
            text_sha256=c.text_sha256,
            token_count=c.token_count,
            char_count=c.char_count,
            page_from=c.page_from,
            page_to=c.page_to,
            section_path=c.section_path,
            meta=dict(c.meta),
        )
        session.add(chunk_row)
        await session.flush()
        chunks_written += 1

        emb = Embedding(
            tenant_id=source.tenant_id,
            entity_type="knowledge_chunk",
            entity_id=chunk_row.id,
            vector=vec,
            model_name=source.embed_model,
            model_version="v1",
            source_text=c.text[:2048],
        )
        session.add(emb)
        await session.flush()
        embeddings_written += 1

    # 8. Audit
    await audit(
        session,
        action="knowledge.ingested",
        tenant_id=source.tenant_id,
        target=("knowledge_documents", doc.id),
        after={
            "scope": source.scope,
            "chunk_count": chunks_written,
            "is_sensitive": is_sensitive,
            "redactions": [
                {"kind": h.kind, "count": h.count} for h in hits
            ],
            "tags": list(source.tags),
        },
        reason=title,
    )

    write_ms = int((time.monotonic() - t2) * 1000)

    cost = (
        estimate_cost_usd(tokens=tokens_embedded, model=source.embed_model)
        if tokens_embedded > 0
        else Decimal("0.000000")
    )

    logger.info(
        "knowledge.ingest.complete",
        extra={
            "document_id": str(doc.id),
            "chunk_count": chunks_written,
            "is_sensitive": is_sensitive,
            "cost_usd": str(cost),
            "extract_ms": extract_ms,
            "embed_ms": embed_ms,
            "write_ms": write_ms,
        },
    )

    return IngestionResult(
        document_id=doc.id,
        is_duplicate=False,
        is_sensitive=is_sensitive,
        chunk_count=chunks_written,
        embedding_count=embeddings_written,
        redactions=hits,
        tokens_embedded=tokens_embedded,
        cost_usd=cost,
        extract_ms=extract_ms,
        embed_ms=embed_ms,
        write_ms=write_ms,
    )


# ─── helpers ────────────────────────────────────────────────────────────


async def _load_source_bytes(source: IngestionSource) -> bytes:
    """Resolve the source reference to raw bytes.

    NOTE: HTTP + Drive adapters are Phase 2. This module refuses them
    for now with a clear error so callers see the boundary.
    """
    if source.inline_bytes_b64 is not None:
        try:
            return base64.b64decode(source.inline_bytes_b64, validate=True)
        except Exception as exc:
            raise IngestionDeniedError(
                f"invalid base64 in inline_bytes_b64: {exc}"
            ) from exc
    if source.file_path is not None:
        p = Path(source.file_path)
        try:
            return p.read_bytes()
        except OSError as exc:
            raise IngestionDeniedError(
                f"unable to read file_path: {exc}"
            ) from exc
    if source.url is not None:
        raise IngestionDeniedError(
            "url adapter not implemented in this slice (Phase 2)"
        )
    if source.drive_file_id is not None:
        raise IngestionDeniedError(
            "drive_file_id adapter not implemented in this slice (Phase 2)"
        )
    raise IngestionDeniedError("no source reference set (guarded by validator)")


def _source_type_for(source: IngestionSource) -> str:
    if source.inline_bytes_b64 is not None:
        return "markdown"  # inline defaults to markdown; caller can override in meta
    if source.file_path is not None:
        p = Path(source.file_path)
        ext = p.suffix.lower()
        return {
            ".pdf": "pdf",
            ".md": "markdown",
            ".txt": "markdown",
            ".docx": "docx",
            ".pptx": "pptx",
            ".xlsx": "xlsx",
            ".html": "html",
        }.get(ext, "other")
    if source.drive_file_id is not None:
        return "google_doc"
    if source.url is not None:
        return "html"
    return "other"


def _source_uri_for(source: IngestionSource) -> str:
    if source.file_path is not None:
        return f"file://{source.file_path}"
    if source.url is not None:
        return source.url
    if source.drive_file_id is not None:
        return f"drive://{source.drive_file_id}"
    # inline
    return "inline://ingested"


def _mime_for_source(source: IngestionSource) -> str | None:
    if source.file_path is not None:
        ext = Path(source.file_path).suffix.lower()
        return {
            ".pdf": "application/pdf",
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".html": "text/html",
        }.get(ext)
    return "text/markdown"


def _title_from(source: IngestionSource, raw: bytes) -> str:
    if source.file_path is not None:
        return Path(source.file_path).name[:512]
    # Fall back to first heading in raw markdown, else timestamp.
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()[:512] or "untitled"
    return "untitled"


def _dummy_uuid() -> UUID:
    """Stable throwaway id for dry_run so the response type stays honest."""
    return UUID(int=0)


__all__: list[str] = ["ingest_source"]

# Silence "imported but unused" for Any which is re-exported for typing.
_reexport: Any = None
