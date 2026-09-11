"""knowledge base schema (BUFF OS Week 1)

Adds three tables for the RAG corpus that replaces the NotebookLM MCP
bridge in the BUFF OS design:

- ``knowledge_documents`` — one row per source artifact (PDF, Markdown,
  Drive file, Notion page, email thread). Idempotent on
  ``(tenant_id, sha256)`` via partial unique index that ignores
  soft-deleted rows.
- ``knowledge_chunks`` — retrievable text shards. Vector embeddings are
  NOT stored here; they live in the existing ``embeddings`` table via
  the ``(entity_type='knowledge_chunk', entity_id=chunk.id)`` polymorphic
  pointer.
- ``knowledge_queries`` — append-only ledger of every RAG question asked.
  Sits alongside ``stock_movements``, ``audit_log`` and
  ``customer_points_ledger`` in the append-only ledger family; UPDATE
  and DELETE are silently no-ops via ``INSTEAD NOTHING`` rules.

See ``specs/knowledge_schema.md`` for full acceptance criteria.

Revision ID: 8b3f2a7c9e14
Revises: 41772db92af3
Create Date: 2026-06-27 12:00:00.000000+08:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8b3f2a7c9e14"
down_revision: str | Sequence[str] | None = "41772db92af3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── knowledge_documents ─────────────────────────────────────────────
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "FUNDING",
                "ENGINEERING",
                "SOP",
                "BRAND",
                "COMPETE",
                "SUBSIDY",
                "SYSTEM",
                "GENERAL",
                name="knowledge_scope",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "is_sensitive",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "byte_size >= 0", name="ck_knowledge_documents_byte_size"
        ),
        sa.CheckConstraint(
            "length(sha256) = 64", name="ck_knowledge_documents_sha_len"
        ),
        sa.CheckConstraint(
            "source_type IN ("
            "'pdf','markdown','docx','pptx','xlsx','html',"
            "'google_doc','notion','line_chat','email','other')",
            name="ck_knowledge_documents_source_type",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_documents_tenant_id"),
        "knowledge_documents",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_scope",
        "knowledge_documents",
        ["scope"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_tenant_scope_ingested",
        "knowledge_documents",
        ["tenant_id", "scope", "ingested_at"],
        unique=False,
    )
    # Partial unique: soft-deleted rows are excluded so a re-upload after
    # a soft delete succeeds with a new id.
    op.create_index(
        "uq_knowledge_documents_tenant_sha256",
        "knowledge_documents",
        ["tenant_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_knowledge_documents_meta",
        "knowledge_documents",
        ["meta"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_knowledge_documents_tags",
        "knowledge_documents",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )

    # ─── knowledge_chunks ────────────────────────────────────────────────
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("section_path", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("chunk_idx >= 0", name="ck_knowledge_chunks_idx"),
        sa.CheckConstraint(
            "token_count >= 0", name="ck_knowledge_chunks_tokens"
        ),
        sa.CheckConstraint("char_count >= 0", name="ck_knowledge_chunks_chars"),
        sa.CheckConstraint(
            "length(text) > 0", name="ck_knowledge_chunks_text_nonempty"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_chunks_tenant_id"),
        "knowledge_chunks",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_chunks_document_id"),
        "knowledge_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "uq_knowledge_chunks_document_idx",
        "knowledge_chunks",
        ["document_id", "chunk_idx"],
        unique=True,
    )
    op.create_index(
        "uq_knowledge_chunks_tenant_textsha",
        "knowledge_chunks",
        ["tenant_id", "text_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_knowledge_chunks_token_count",
        "knowledge_chunks",
        ["token_count"],
        unique=False,
    )

    # ─── knowledge_queries (APPEND-ONLY) ─────────────────────────────────
    op.create_table(
        "knowledge_queries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column(
            "scope_filter",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "tag_filter",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "retrieved_chunk_ids",
            postgresql.ARRAY(sa.UUID()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "retrieved_scores",
            postgresql.ARRAY(sa.Numeric(precision=8, scale=6)),
            server_default=sa.text("'{}'::numeric[]"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column(
            "answer_citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "was_blocked",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        # NB: only ``created_at``; append-only ledger has no ``updated_at``.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_kind IN ('claude','codex','human','cron','mcp')",
            name="ck_knowledge_queries_actor_kind",
        ),
        sa.CheckConstraint(
            "array_length(retrieved_chunk_ids, 1) "
            "IS NOT DISTINCT FROM "
            "array_length(retrieved_scores, 1)",
            name="ck_knowledge_queries_arrays_aligned",
        ),
        sa.CheckConstraint(
            "(was_blocked = false) OR (blocked_reason IS NOT NULL)",
            name="ck_knowledge_queries_blocked_has_reason",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_queries_tenant_id"),
        "knowledge_queries",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_queries_tenant_actor_time",
        "knowledge_queries",
        ["tenant_id", "actor_kind", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_queries_tenant_time",
        "knowledge_queries",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_queries_chunk_ids",
        "knowledge_queries",
        ["retrieved_chunk_ids"],
        unique=False,
        postgresql_using="gin",
    )

    # ─── Append-only enforcement (defense-in-depth) ───────────────────────
    # ``INSTEAD NOTHING`` silently drops UPDATE/DELETE — matches the
    # convention in the closed-loop migration for stock_movements /
    # audit_log / customer_points_ledger / customer_stored_value_ledger.
    _t = "knowledge_queries"
    op.execute(
        f"CREATE RULE no_update_{_t} AS ON UPDATE TO {_t} DO INSTEAD NOTHING"
    )
    op.execute(
        f"CREATE RULE no_delete_{_t} AS ON DELETE TO {_t} DO INSTEAD NOTHING"
    )


def downgrade() -> None:
    # Drop the append-only rules first, otherwise the table drop below
    # would fail on the rule dependency.
    _t = "knowledge_queries"
    op.execute(f"DROP RULE IF EXISTS no_update_{_t} ON {_t}")
    op.execute(f"DROP RULE IF EXISTS no_delete_{_t} ON {_t}")

    # Drop in reverse dependency order.
    op.drop_index(
        "ix_knowledge_queries_chunk_ids", table_name="knowledge_queries"
    )
    op.drop_index(
        "ix_knowledge_queries_tenant_time", table_name="knowledge_queries"
    )
    op.drop_index(
        "ix_knowledge_queries_tenant_actor_time", table_name="knowledge_queries"
    )
    op.drop_index(
        op.f("ix_knowledge_queries_tenant_id"), table_name="knowledge_queries"
    )
    op.drop_table("knowledge_queries")

    op.drop_index(
        "ix_knowledge_chunks_token_count", table_name="knowledge_chunks"
    )
    op.drop_index(
        "uq_knowledge_chunks_tenant_textsha", table_name="knowledge_chunks"
    )
    op.drop_index(
        "uq_knowledge_chunks_document_idx", table_name="knowledge_chunks"
    )
    op.drop_index(
        op.f("ix_knowledge_chunks_document_id"), table_name="knowledge_chunks"
    )
    op.drop_index(
        op.f("ix_knowledge_chunks_tenant_id"), table_name="knowledge_chunks"
    )
    op.drop_table("knowledge_chunks")

    op.drop_index(
        "ix_knowledge_documents_tags", table_name="knowledge_documents"
    )
    op.drop_index(
        "ix_knowledge_documents_meta", table_name="knowledge_documents"
    )
    op.drop_index(
        "uq_knowledge_documents_tenant_sha256",
        table_name="knowledge_documents",
    )
    op.drop_index(
        "ix_knowledge_documents_tenant_scope_ingested",
        table_name="knowledge_documents",
    )
    op.drop_index(
        "ix_knowledge_documents_scope", table_name="knowledge_documents"
    )
    op.drop_index(
        op.f("ix_knowledge_documents_tenant_id"),
        table_name="knowledge_documents",
    )
    op.drop_table("knowledge_documents")
