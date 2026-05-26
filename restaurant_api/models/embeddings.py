"""Generic embeddings table — the seed for the AI data asset (Phase 2+).

One row per (entity_type, entity_id, model_version). Same physical table
serves multiple use cases:

- ``menu_item`` — semantic search ("給我推薦類似牛排的菜")
- ``customer`` — clustering / lookalike modelling for marketing
- ``order_pattern`` — anomaly detection (this order looks unlike anything
  this customer has bought before)
- ``ingredient`` — substitution suggestions when something's out of stock

We expose this in Phase 0 so the schema is stable from day one. The
nightly embedding pipeline ships in Phase 2; until then this table will
mostly be empty, which is fine.

Uses pgvector — extension must be loaded in the target DB
(``CREATE EXTENSION vector;``). Initial dimension = 1536 (OpenAI / Voyage
sizes); reduce later if we settle on a smaller-dimensional model.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantScopedMixin, uuid7

VECTOR_DIM = 1536


class Embedding(TenantScopedMixin, Base):
    """Single embedding row — vector + provenance."""

    __tablename__ = "embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # Polymorphic pointer — kept as (type, id) pair rather than FK so we can
    # embed entities that don't have ORM tables yet (e.g. ``order_pattern``).
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # The actual vector. pgvector exposes <-> (l2), <=> (cosine), <#> (inner
    # product) operators for similarity queries.
    vector: Mapped[list[float]] = mapped_column(Vector(VECTOR_DIM), nullable=False)
    # Provenance: which model generated this? We keep both so we can swap
    # providers (Voyage / Cohere / Anthropic) without losing history.
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Source text (or short summary) for debugging / re-embedding.
    source_text: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # One embedding per (entity, model_version) — re-embedding the same
        # entity with the same model is a no-op upsert target.
        Index(
            "uq_embeddings_entity_model",
            "tenant_id",
            "entity_type",
            "entity_id",
            "model_name",
            "model_version",
            unique=True,
        ),
        Index("ix_embeddings_type_time", "entity_type", "created_at"),
        # ANN index — IVFFlat is the lightweight choice for ≤1M rows.
        # Switch to HNSW (postgresql_using="hnsw") once we cross 1M rows
        # or hit query-latency issues.
        Index(
            "ix_embeddings_vector",
            "vector",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )


__all__ = ["VECTOR_DIM", "Embedding"]
