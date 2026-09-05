"""Knowledge base — RAG-ready document store for BUFF OS.

Three tables land here:

- ``knowledge_documents`` — one row per source artifact (PDF, Markdown,
  Drive file, Notion page, LINE chat dump, investor email thread).
- ``knowledge_chunks``    — text shards of a document; the retrieval unit.
- ``knowledge_queries``   — append-only ledger of every RAG question that
  was asked of this corpus, who asked, what we retrieved, what we answered.

DESIGN NOTE — embeddings live elsewhere
---------------------------------------
We deliberately do **not** add a ``vector`` column to ``knowledge_chunks``.
The existing ``embeddings`` table (see ``embeddings.py``) is the canonical
vector store for every embeddable entity in the system; chunks plug in via
the polymorphic ``(entity_type='knowledge_chunk', entity_id=<chunks.id>)``
pointer. Reasons:

1. One ANN index serves all embeddable entities (menu_item / customer /
   knowledge_chunk / order_pattern …). Cheaper to maintain, faster to query.
2. The same chunk can be embedded by multiple model versions in parallel
   (A/B model evaluations) — embeddings already key by ``(model_name,
   model_version)`` for exactly this.
3. Swapping the embedding provider does not require a schema migration on
   ``knowledge_chunks``.

Retrieval therefore JOINs::

    SELECT c.* FROM embeddings e
    JOIN knowledge_chunks c ON c.id = e.entity_id
    WHERE e.entity_type = 'knowledge_chunk'
      AND e.tenant_id   = :tid
      AND e.model_name  = :model AND e.model_version = :version
    ORDER BY e.vector <=> :query_vec
    LIMIT :k;

DESIGN NOTE — append-only queries
---------------------------------
``knowledge_queries`` is a ledger, not state. It is the audit trail every
time a Claude / Codex / human agent asks the corpus a question — what was
asked, what we retrieved, what we answered, how long it took, whether the
deny-list blocked it. Same family as ``stock_movements``, ``audit_log``,
``customer_points_ledger``: enforced append-only at the DB level via
``ON UPDATE / ON DELETE DO INSTEAD NOTHING`` rules installed by the
migration. We mirror the intent in the ORM by **not** inheriting
``TimestampedMixin`` (no ``updated_at``) and by exposing no update path.

See ``specs/knowledge_schema.md`` for full schema spec and acceptance
criteria.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)

# Aliased so it doesn't collide with the ``text: Mapped[str]`` column on
# ``KnowledgeChunk`` — class body scope would otherwise shadow it.
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)

# ---------------------------------------------------------------------------
# Closed-set source_type for knowledge_documents.source_type
# ---------------------------------------------------------------------------

# Kept as a python tuple (not a PG enum) on purpose: this is the set of
# verbs an ingestion adapter can produce, and we'd rather grow it without
# a migration. Validation lives at:
#   * the Pydantic input layer in services/knowledge_ingestion
#   * a DB-level CHECK installed by the migration
SOURCE_TYPES = (
    "pdf",
    "markdown",
    "docx",
    "pptx",
    "xlsx",
    "html",
    "google_doc",
    "notion",
    "line_chat",
    "email",
    "other",
)


# ---------------------------------------------------------------------------
# Closed-set actor_kind for knowledge_queries.actor_kind
# ---------------------------------------------------------------------------

ACTOR_KINDS = ("claude", "codex", "human", "cron", "mcp")


# ---------------------------------------------------------------------------
# KnowledgeScope — which battlefront a document/query belongs to
# ---------------------------------------------------------------------------


class KnowledgeScope(enum.StrEnum):
    """Battlefront partition for the knowledge base.

    Mirrors the 7-notebook structure from the BUFF OS plan: keep the
    investor-facing corpus separate from the engineering one separate from
    the brand one, so retrieval can be filter-scoped without cross-talk.
    ``GENERAL`` is the fall-back bucket; it should remain small.
    """

    FUNDING = "funding"
    ENGINEERING = "engineering"
    SOP = "sop"
    BRAND = "brand"
    COMPETE = "compete"
    SUBSIDY = "subsidy"
    SYSTEM = "system"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# knowledge_documents
# ---------------------------------------------------------------------------


class KnowledgeDocument(
    TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base
):
    """One source artifact = one row.

    Idempotency key is ``(tenant_id, sha256)`` via a partial unique index
    that ignores soft-deleted rows — re-ingesting the same bytes after a
    soft delete is allowed (and produces a fresh document_id).
    """

    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    scope: Mapped[KnowledgeScope] = mapped_column(
        SQLEnum(
            KnowledgeScope,
            name="knowledge_scope",
            native_enum=False,
            length=24,
        ),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # SHA-256 of the original bytes, lower-case hex.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # BCP-47, e.g. "zh-Hant", "en".
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    source_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
        default=dict,
    )
    # True iff the deny-list matched at ingestion time OR the caller
    # marked the source as sensitive explicitly. We still store the
    # document so we can audit "did we accidentally ingest a contract?";
    # the retrieval layer is what enforces who can see it.
    is_sensitive: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
        default=list,
    )

    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_knowledge_documents_byte_size"),
        CheckConstraint("length(sha256) = 64", name="ck_knowledge_documents_sha_len"),
        CheckConstraint(
            "source_type IN ("
            "'pdf','markdown','docx','pptx','xlsx','html',"
            "'google_doc','notion','line_chat','email','other')",
            name="ck_knowledge_documents_source_type",
        ),
        # Idempotency: same tenant + same content hash = same logical doc.
        # Partial: soft-deleted rows are excluded so a re-upload after a
        # soft delete succeeds with a new id.
        Index(
            "uq_knowledge_documents_tenant_sha256",
            "tenant_id",
            "sha256",
            unique=True,
            postgresql_where=sql_text("deleted_at IS NULL"),
        ),
        # Retrieval listing: "give me the latest 50 docs in funding scope"
        Index(
            "ix_knowledge_documents_tenant_scope_ingested",
            "tenant_id",
            "scope",
            "ingested_at",
        ),
        # JSONB containment search on metadata.
        Index(
            "ix_knowledge_documents_meta",
            "meta",
            postgresql_using="gin",
        ),
        # Tag filter.
        Index(
            "ix_knowledge_documents_tags",
            "tags",
            postgresql_using="gin",
        ),
    )


# ---------------------------------------------------------------------------
# knowledge_chunks
# ---------------------------------------------------------------------------


class KnowledgeChunk(TenantScopedMixin, TimestampedMixin, Base):
    """A retrievable slice of a document.

    Chunk order within a document is ``chunk_idx`` (0-based, may have gaps
    if the ingester skipped empty pages). Vector lives in ``embeddings``
    — see the module docstring.

    Soft-delete is intentionally NOT inherited: chunks cascade with their
    parent document. If you want to "delete" a chunk, soft-delete the
    document and re-ingest with the chunk removed.
    """

    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    # Already redacted at this point — the raw bytes never reach DB.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
        default=dict,
    )

    __table_args__ = (
        CheckConstraint("chunk_idx >= 0", name="ck_knowledge_chunks_idx"),
        CheckConstraint("token_count >= 0", name="ck_knowledge_chunks_tokens"),
        CheckConstraint("char_count >= 0", name="ck_knowledge_chunks_chars"),
        CheckConstraint("length(text) > 0", name="ck_knowledge_chunks_text_nonempty"),
        # Chunk order is unique within a document.
        Index(
            "uq_knowledge_chunks_document_idx",
            "document_id",
            "chunk_idx",
            unique=True,
        ),
        # Cross-document chunk-level dedup, scoped per tenant: the same
        # boilerplate paragraph appearing in 5 decks gets embedded once.
        Index(
            "uq_knowledge_chunks_tenant_textsha",
            "tenant_id",
            "text_sha256",
            unique=True,
        ),
        # Retrieval budget planner ranges over token_count.
        Index("ix_knowledge_chunks_token_count", "token_count"),
    )


# ---------------------------------------------------------------------------
# knowledge_queries (APPEND-ONLY LEDGER)
# ---------------------------------------------------------------------------


class KnowledgeQuery(TenantScopedMixin, Base):
    """One row per RAG question asked of the corpus.

    THIS TABLE IS IMMUTABLE.

    Append-only is enforced at the DB level by ``INSTEAD NOTHING`` rules
    in the migration (same pattern as ``stock_movements``, ``audit_log``,
    ``customer_points_ledger``). We mirror the intent in the ORM by:

    * NOT inheriting ``TimestampedMixin`` — there is no ``updated_at``;
      the row is born once and never amended.
    * Carrying both ``query_text`` and the resolved retrieval set
      (``retrieved_chunk_ids`` + ``retrieved_scores``) so that the row is
      self-contained even if the underlying chunks are later re-ingested
      or soft-deleted.

    The ``answer_citations`` JSONB column stores structured citations the
    LLM emitted, in the form::

        [{"chunk_id": "...", "doc_id": "...", "score": 0.84,
          "span": [123, 187]}]

    so that the answer can be replayed and audited even if the corpus
    drifts.
    """

    __tablename__ = "knowledge_queries"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The question, already redacted by the ingestion / retrieval layer
    # against the same deny-list as documents.
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    scope_filter: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
        default=list,
    )
    tag_filter: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
        default=list,
    )
    # Two parallel arrays; the CHECK constraint below pins their lengths.
    retrieved_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)),
        nullable=False,
        server_default=sql_text("'{}'::uuid[]"),
        default=list,
    )
    retrieved_scores: Mapped[list[Decimal]] = mapped_column(
        ARRAY(Numeric(8, 6)),
        nullable=False,
        server_default=sql_text("'{}'::numeric[]"),
        default=list,
    )
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_citations: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
        default=dict,
    )
    # was_blocked=True means the deny-list (or scope filter) refused to
    # answer; ``blocked_reason`` is then required by the CHECK below.
    was_blocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "actor_kind IN ('claude','codex','human','cron','mcp')",
            name="ck_knowledge_queries_actor_kind",
        ),
        CheckConstraint(
            # NULL-safe: either both arrays are empty / NULL with same length
            # signature, or both have the same array_length.
            "array_length(retrieved_chunk_ids, 1) IS NOT DISTINCT FROM "
            "array_length(retrieved_scores, 1)",
            name="ck_knowledge_queries_arrays_aligned",
        ),
        CheckConstraint(
            "(was_blocked = false) OR (blocked_reason IS NOT NULL)",
            name="ck_knowledge_queries_blocked_has_reason",
        ),
        Index(
            "ix_knowledge_queries_tenant_actor_time",
            "tenant_id",
            "actor_kind",
            "created_at",
        ),
        Index(
            "ix_knowledge_queries_tenant_time",
            "tenant_id",
            "created_at",
        ),
        # Reverse lookup: "which queries cited this chunk?"
        Index(
            "ix_knowledge_queries_chunk_ids",
            "retrieved_chunk_ids",
            postgresql_using="gin",
        ),
    )


__all__ = [
    "ACTOR_KINDS",
    "SOURCE_TYPES",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeQuery",
    "KnowledgeScope",
]
