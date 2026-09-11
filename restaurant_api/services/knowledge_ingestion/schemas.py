"""Pydantic v2 request / response schemas for the ingestion service.

See ``specs/knowledge_ingestion.md`` §4 and §5 for the contract.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .redact import RedactionHit

Scope = Literal[
    "funding",
    "engineering",
    "sop",
    "brand",
    "compete",
    "subsidy",
    "system",
    "general",
]

EmbedModel = Literal[
    "voyage-3-large",
    "voyage-3",
    "text-embedding-3-large",
    "text-embedding-3-small",
]


class IngestionSource(BaseModel):
    """One-shot ingestion request. Exactly one of ``file_path`` /
    ``url`` / ``drive_file_id`` / ``inline_bytes_b64`` must be set.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    tenant_id: UUID
    scope: Scope

    # exactly-one source reference
    file_path: str | None = Field(default=None, max_length=1024)
    url: str | None = Field(default=None, max_length=2048, pattern=r"^https://")
    drive_file_id: str | None = Field(default=None, max_length=128)
    inline_bytes_b64: str | None = None

    title: str | None = Field(default=None, max_length=512)
    tags: list[str] = Field(default_factory=list, max_length=32)
    source_modified_at: datetime | None = None
    explicit_sensitive: bool = False

    max_tokens_per_chunk: int = Field(default=512, ge=64, le=4096)
    chunk_overlap_tokens: int = Field(default=64, ge=0, le=512)
    embed_model: EmbedModel = "voyage-3-large"
    dry_run: bool = False

    @model_validator(mode="after")
    def _validate(self) -> IngestionSource:
        refs = [
            self.file_path,
            self.url,
            self.drive_file_id,
            self.inline_bytes_b64,
        ]
        set_count = sum(1 for r in refs if r is not None)
        if set_count != 1:
            raise ValueError(
                "exactly one of file_path / url / drive_file_id / "
                "inline_bytes_b64 must be set"
            )
        if self.source_modified_at is not None and (
            self.source_modified_at.tzinfo is None
        ):
            raise ValueError("source_modified_at must be tz-aware")
        if self.chunk_overlap_tokens >= self.max_tokens_per_chunk:
            raise ValueError(
                "chunk_overlap_tokens must be < max_tokens_per_chunk"
            )
        # normalize tags: strip + lowercase; reject whitespace-only
        normalized: list[str] = []
        for t in self.tags:
            s = t.strip()
            if not s:
                raise ValueError("tag must not be whitespace-only")
            if len(s) > 64:
                raise ValueError("tag must be <= 64 chars")
            normalized.append(s.lower())
        # Frozen model — set via object.__setattr__ to preserve immutability
        # for downstream callers while normalising once at construction.
        object.__setattr__(self, "tags", normalized)
        return self


class IngestionResult(BaseModel):
    """Return value of ``ingest_source``.

    ``cost_usd`` uses ``Decimal`` at 6dp; zero when the ingestion was
    a duplicate or dry_run.
    """

    model_config = ConfigDict(frozen=True)

    document_id: UUID
    is_duplicate: bool
    is_sensitive: bool
    chunk_count: int
    embedding_count: int
    redactions: list[RedactionHit]
    tokens_embedded: int
    cost_usd: Decimal
    extract_ms: int
    embed_ms: int
    write_ms: int


__all__ = [
    "EmbedModel",
    "IngestionResult",
    "IngestionSource",
    "Scope",
]
