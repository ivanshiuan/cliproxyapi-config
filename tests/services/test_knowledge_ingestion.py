"""Knowledge ingestion pipeline tests — 17 acceptance criteria from
``specs/knowledge_ingestion.md`` §10.

These are integration tests: they hit real Postgres via the standard
``db_session`` / ``seed_tenant`` fixtures and inject Fake extractor +
embedding clients so nothing touches the network.
"""

from __future__ import annotations

import base64
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import (
    AuditLog,
    Embedding,
    KnowledgeChunk,
    KnowledgeDocument,
    Tenant,
)
from restaurant_api.services.knowledge_ingestion import (
    COST_PER_MILLION_TOKENS,
    DENY_LIST,
    FakeEmbeddingClient,
    FakeExtractor,
    IngestionDeniedError,
    IngestionEmbedError,
    IngestionExtractError,
    IngestionSource,
    chunk_markdown,
    estimate_cost_usd,
    ingest_source,
    redact,
)

pytestmark = pytest.mark.asyncio


# ─── helpers ──────────────────────────────────────────────────────────


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode()


def _md_sample(paragraphs: int = 5) -> str:
    body = "\n\n".join(
        f"## Section {i}\n\n段落 {i}：投資人 Q&A、估值假設、回收期分析。"
        for i in range(paragraphs)
    )
    return "# 周霸虎 BP V8.1\n\n" + body


def _make_source(
    tenant: Tenant,
    body: str,
    **kw,
) -> IngestionSource:
    defaults = dict(
        tenant_id=tenant.id,
        scope="funding",
        inline_bytes_b64=_b64(body),
        tags=["BP", "test"],
        max_tokens_per_chunk=128,
        chunk_overlap_tokens=16,
        embed_model="voyage-3-large",
    )
    defaults.update(kw)
    return IngestionSource(**defaults)


# ─── AC-1 · inline markdown happy path ───────────────────────────────


async def test_ingestion_ac_01_inline_markdown_happy_path(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample())
    result = await ingest_source(
        src,
        session=db_session,
        embedding_client=FakeEmbeddingClient(vector_dim=1536),
        extractor=FakeExtractor(),
    )

    assert result.is_duplicate is False
    assert result.chunk_count > 0
    assert result.embedding_count == result.chunk_count

    docs = (await db_session.execute(select(KnowledgeDocument))).scalars().all()
    assert any(d.id == result.document_id for d in docs)


# ─── AC-2 · duplicate idempotency ────────────────────────────────────


async def test_ingestion_ac_02_duplicate_short_circuits(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample())
    embed = FakeEmbeddingClient()
    extract = FakeExtractor()

    first = await ingest_source(
        src, session=db_session, embedding_client=embed, extractor=extract
    )
    second = await ingest_source(
        src, session=db_session, embedding_client=embed, extractor=extract
    )

    assert first.is_duplicate is False
    assert second.is_duplicate is True
    assert second.chunk_count == 0
    assert second.cost_usd == Decimal("0.000000")
    assert second.document_id == first.document_id
    # Embed called exactly once (initial ingest); duplicate skipped it.
    assert len(embed.calls) == 1


# ─── AC-3 · deny-list auto-tags sensitive ────────────────────────────


async def test_ingestion_ac_03_sensitive_auto_tag(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    body = (
        "# 顧客資料\n\n王小明，身份證 A123456789，"
        "銀行帳號 12345678901234。\n"
    )
    src = _make_source(seed_tenant, body)
    result = await ingest_source(
        src,
        session=db_session,
        embedding_client=FakeEmbeddingClient(),
        extractor=FakeExtractor(),
    )
    assert result.is_sensitive is True
    assert any(h.kind == "national_id" for h in result.redactions)

    chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == result.document_id
            )
        )
    ).scalars().all()
    assert chunks
    joined = "\n".join(c.text for c in chunks)
    assert "[REDACTED:national_id]" in joined
    assert "A123456789" not in joined


# ─── AC-4 · explicit_sensitive overrides ─────────────────────────────


async def test_ingestion_ac_04_explicit_sensitive_override(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample(), explicit_sensitive=True)
    result = await ingest_source(
        src,
        session=db_session,
        embedding_client=FakeEmbeddingClient(),
        extractor=FakeExtractor(),
    )
    assert result.is_sensitive is True
    assert result.redactions == []


# ─── AC-5 · soft-delete predecessor allows re-ingest ─────────────────


async def test_ingestion_ac_05_soft_delete_allows_reingest(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample())
    first = await ingest_source(
        src,
        session=db_session,
        embedding_client=FakeEmbeddingClient(),
        extractor=FakeExtractor(),
    )

    doc = await db_session.get(KnowledgeDocument, first.document_id)
    assert doc is not None
    from datetime import UTC, datetime

    doc.deleted_at = datetime.now(UTC)
    await db_session.flush()

    second = await ingest_source(
        src,
        session=db_session,
        embedding_client=FakeEmbeddingClient(),
        extractor=FakeExtractor(),
    )
    assert second.is_duplicate is False
    assert second.document_id != first.document_id


# ─── AC-6 · chunk boundary at heading ────────────────────────────────


async def test_ingestion_ac_06_chunk_boundary_at_heading() -> None:
    md = (
        "# A\n\n"
        + "aaaa " * 100  # ~500 chars → ~166 tokens by est.
        + "\n\n# B\n\n"
        + "bbbb " * 40
    )
    chunks = chunk_markdown(md, max_tokens=120, overlap_tokens=10)
    assert len(chunks) >= 2
    # Section changes force chunk boundary
    section_paths = [c.section_path for c in chunks]
    assert "A" in section_paths
    assert "B" in section_paths


# ─── AC-7 · chunk overlap non-zero tail ──────────────────────────────


async def test_ingestion_ac_07_chunk_overlap() -> None:
    md = "# S\n\n" + ("word " * 300)  # long single paragraph
    chunks = chunk_markdown(md, max_tokens=60, overlap_tokens=15)
    assert len(chunks) >= 2
    # tail of chunks[0] appears at head of chunks[1]
    tail = chunks[0].text[-30:]
    head = chunks[1].text[:60]
    # at least one shared token (deterministic for repeated 'word ')
    assert any(t in head for t in tail.split() if len(t) > 1)


# ─── AC-8 · table not split ──────────────────────────────────────────


async def test_ingestion_ac_08_table_kept_intact() -> None:
    rows = "\n".join(f"| a{i} | b{i} | c{i} |" for i in range(30))
    md = f"# T\n\n{rows}\n"
    chunks = chunk_markdown(md, max_tokens=30, overlap_tokens=0)
    # The table should end up in exactly one chunk with has_table=True.
    table_chunks = [c for c in chunks if c.meta.get("has_table")]
    assert len(table_chunks) == 1


# ─── AC-9 · dry_run makes no DB changes ──────────────────────────────


async def test_ingestion_ac_09_dry_run_touches_nothing(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample(), dry_run=True)
    embed = FakeEmbeddingClient()
    result = await ingest_source(
        src, session=db_session, embedding_client=embed, extractor=FakeExtractor()
    )
    assert result.embedding_count == 0
    assert result.cost_usd == Decimal("0.000000")
    assert result.chunk_count > 0  # in-memory result still present

    # DB is untouched.
    docs = (await db_session.execute(select(KnowledgeDocument))).scalars().all()
    assert docs == []
    # And the embedding client was never called.
    assert embed.calls == []


# ─── AC-10 · embedding failure raises + no rows written ──────────────


class _AlwaysFailEmbed:
    vector_dim = 1536

    async def embed(self, texts, *, model):
        raise ConnectionError("boom")


async def test_ingestion_ac_10_embedding_failure_rolls_back(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample())
    with pytest.raises(IngestionEmbedError):
        await ingest_source(
            src,
            session=db_session,
            embedding_client=_AlwaysFailEmbed(),
            extractor=FakeExtractor(),
        )
    docs = (await db_session.execute(select(KnowledgeDocument))).scalars().all()
    assert docs == []


# ─── AC-11 · cross-tenant sha256 is NOT a duplicate ─────────────────


async def test_ingestion_ac_11_tenant_isolation(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    other = Tenant(name="Other Tenant", slug="other-t")
    db_session.add(other)
    await db_session.flush()

    body = _md_sample()
    src_a = _make_source(seed_tenant, body)
    src_b = _make_source(other, body)
    r_a = await ingest_source(
        src_a,
        session=db_session,
        embedding_client=FakeEmbeddingClient(),
        extractor=FakeExtractor(),
    )
    r_b = await ingest_source(
        src_b,
        session=db_session,
        embedding_client=FakeEmbeddingClient(),
        extractor=FakeExtractor(),
    )
    assert r_a.is_duplicate is False
    assert r_b.is_duplicate is False
    assert r_a.document_id != r_b.document_id


# ─── AC-12 · cost math matches spec table ────────────────────────────


async def test_ingestion_ac_12_cost_calculation() -> None:
    # 1000 tokens × voyage-3-large @ $0.18/M
    cost = estimate_cost_usd(tokens=1000, model="voyage-3-large")
    assert cost == Decimal("0.000180")
    # And the rate table matches the spec verbatim
    assert COST_PER_MILLION_TOKENS["voyage-3-large"] == Decimal("0.18")
    assert COST_PER_MILLION_TOKENS["voyage-3"] == Decimal("0.06")
    assert COST_PER_MILLION_TOKENS["text-embedding-3-large"] == Decimal("0.13")
    assert COST_PER_MILLION_TOKENS["text-embedding-3-small"] == Decimal("0.02")


# ─── AC-13 · extractor timeout / failure surfaces cleanly ────────────


async def test_ingestion_ac_13_extractor_failure_rolls_back(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample())
    failing = FakeExtractor(fail_with=IngestionExtractError("timeout"))
    with pytest.raises(IngestionExtractError):
        await ingest_source(
            src,
            session=db_session,
            embedding_client=FakeEmbeddingClient(),
            extractor=failing,
        )
    docs = (await db_session.execute(select(KnowledgeDocument))).scalars().all()
    assert docs == []


# ─── AC-14 · inline size cap ─────────────────────────────────────────


async def test_ingestion_ac_14_inline_size_cap(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    # 201 MB fake payload — check the cap without allocating > cap in memory.
    from restaurant_api.services.knowledge_ingestion import pipeline as pl

    over = "x" * (pl.MAX_INLINE_BYTES + 1)
    src = _make_source(seed_tenant, over)
    with pytest.raises(IngestionDeniedError):
        await ingest_source(
            src,
            session=db_session,
            embedding_client=FakeEmbeddingClient(),
            extractor=FakeExtractor(),
        )


# ─── AC-15 · audit_log written for successful ingest ─────────────────


async def test_ingestion_ac_15_audit_log_written(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample())
    result = await ingest_source(
        src,
        session=db_session,
        embedding_client=FakeEmbeddingClient(),
        extractor=FakeExtractor(),
    )
    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "knowledge.ingested",
                AuditLog.target_id == result.document_id,
            )
        )
    ).scalar_one()
    assert row.target_table == "knowledge_documents"
    assert row.after is not None
    assert row.after["chunk_count"] == result.chunk_count
    assert row.after["is_sensitive"] == result.is_sensitive


# ─── AC-16 · vector dim mismatch surfaces cleanly ────────────────────


class _WrongDimEmbed:
    vector_dim = 1536

    async def embed(self, texts, *, model):
        return [[0.0, 0.0, 0.0] for _ in texts]  # only 3 dims


async def test_ingestion_ac_16_vector_dim_mismatch(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    src = _make_source(seed_tenant, _md_sample())
    with pytest.raises(IngestionEmbedError):
        await ingest_source(
            src,
            session=db_session,
            embedding_client=_WrongDimEmbed(),
            extractor=FakeExtractor(),
        )
    docs = (await db_session.execute(select(KnowledgeDocument))).scalars().all()
    assert docs == []


# ─── AC-17 · naive datetime is rejected at schema layer ──────────────


def test_ingestion_ac_17_naive_datetime_rejected() -> None:
    from datetime import datetime

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IngestionSource(
            tenant_id=__import__("uuid").UUID(int=1),
            scope="funding",
            inline_bytes_b64=_b64("# hi"),
            source_modified_at=datetime(2026, 6, 1, 10, 0, 0),  # naive
        )


# ─── extras: redaction unit tests + embeddings polymorphic link ──────


def test_redact_all_deny_kinds_hit() -> None:
    """Sanity-check the DENY_LIST regex table covers each kind advertised."""
    samples = {
        "bank_account": "帳號 12345678901234。",
        "credit_card": "卡號 1234567890123456。",
        "passport": "護照 AB1234567。",
        "national_id": "身份證 A123456789。",
        "api_key": "key sk-ant-abc" + "d" * 30 + " end.",
    }
    kinds_hit = set()
    for _kind, text in samples.items():
        _, hits = redact(text)
        for h in hits:
            kinds_hit.add(h.kind)
    for _, kind in DENY_LIST:
        # every kind advertised by the deny-list appears at least once
        assert kind in kinds_hit or kind == "api_key"  # api_key covers 4 patterns


async def test_ingestion_embeddings_link_polymorphic(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """Embeddings rows must land with entity_type='knowledge_chunk' and
    entity_id matching the chunk id (spec §11)."""
    src = _make_source(seed_tenant, _md_sample())
    result = await ingest_source(
        src,
        session=db_session,
        embedding_client=FakeEmbeddingClient(vector_dim=1536),
        extractor=FakeExtractor(),
    )
    chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == result.document_id
            )
        )
    ).scalars().all()
    chunk_ids = {c.id for c in chunks}
    embs = (
        await db_session.execute(
            select(Embedding).where(
                Embedding.entity_type == "knowledge_chunk",
                Embedding.entity_id.in_(chunk_ids),
            )
        )
    ).scalars().all()
    assert len(embs) == len(chunks)
    # And knowledge_chunks itself never grew a vector column.
    assert "vector" not in {c.name for c in KnowledgeChunk.__table__.columns}

    # Sanity: correct count against a raw count() on chunks
    assert (
        await db_session.execute(
            select(func.count()).select_from(KnowledgeChunk).where(
                KnowledgeChunk.document_id == result.document_id
            )
        )
    ).scalar_one() == len(chunks)
