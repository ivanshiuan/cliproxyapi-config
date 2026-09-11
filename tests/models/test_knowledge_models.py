"""Knowledge base ORM tests — 14 acceptance criteria from
``specs/knowledge_schema.md`` §7.

These are integration tests: they hit a real Postgres (per the project
convention documented in ``tests/conftest.py``). Each test rolls back in
a SAVEPOINT so no rows leak.

Covers:

* insert / roundtrip for all 3 tables
* tenant_id required
* sha256 idempotency (partial unique — soft-deleted excluded)
* chunk cascade with parent document
* chunk_idx unique within document
* text_sha256 unique per tenant
* KnowledgeScope enum boundary
* knowledge_queries append-only (UPDATE and DELETE are no-ops)
* retrieved_chunk_ids / retrieved_scores length aligned
* embeddings table reuse via polymorphic pointer
* updated_at bumped on document update
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import (
    Embedding,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeQuery,
    KnowledgeScope,
    Tenant,
)

pytestmark = pytest.mark.asyncio


# ─── helpers ──────────────────────────────────────────────────────────


def _fake_sha256(seed: str = "a") -> str:
    """A 64-char hex string that is deterministic per seed but distinct."""
    return (seed * 64)[:64]


async def _make_document(
    session: AsyncSession,
    tenant: Tenant,
    *,
    sha256: str | None = None,
    scope: KnowledgeScope = KnowledgeScope.FUNDING,
    title: str = "BP V8.1",
    byte_size: int = 1024,
    source_type: str = "pdf",
    is_sensitive: bool = False,
) -> KnowledgeDocument:
    doc = KnowledgeDocument(
        tenant_id=tenant.id,
        scope=scope,
        source_type=source_type,
        source_uri=f"file:///tmp/{title}.pdf",
        title=title,
        sha256=sha256 or _fake_sha256("a"),
        byte_size=byte_size,
        mime_type="application/pdf",
        language="zh-Hant",
        is_sensitive=is_sensitive,
    )
    session.add(doc)
    await session.flush()
    return doc


async def _make_chunk(
    session: AsyncSession,
    tenant: Tenant,
    doc: KnowledgeDocument,
    *,
    idx: int = 0,
    text_body: str = "投資人 Q&A 段落一",
    text_sha256: str | None = None,
    token_count: int = 32,
) -> KnowledgeChunk:
    chunk = KnowledgeChunk(
        tenant_id=tenant.id,
        document_id=doc.id,
        chunk_idx=idx,
        text=text_body,
        text_sha256=text_sha256 or _fake_sha256(f"c{idx}"),
        token_count=token_count,
        char_count=len(text_body),
    )
    session.add(chunk)
    await session.flush()
    return chunk


# ─── AC-1 · three tables accept inserts + roundtrip ───────────────────


async def test_knowledge_ac_01_three_tables_roundtrip(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    doc = await _make_document(db_session, seed_tenant)
    chunk = await _make_chunk(db_session, seed_tenant, doc)
    query = KnowledgeQuery(
        tenant_id=seed_tenant.id,
        actor_kind="claude",
        actor_id="test-session",
        query_text="估值 1350 vs 1400 差別?",
        scope_filter=["funding"],
        retrieved_chunk_ids=[chunk.id],
        retrieved_scores=[0.812345],
    )
    db_session.add(query)
    await db_session.flush()

    got_doc = await db_session.get(KnowledgeDocument, doc.id)
    got_chunk = await db_session.get(KnowledgeChunk, chunk.id)
    got_query = await db_session.get(KnowledgeQuery, query.id)

    assert got_doc is not None and got_doc.title == "BP V8.1"
    assert got_chunk is not None and got_chunk.chunk_idx == 0
    assert got_query is not None and got_query.actor_kind == "claude"
    assert got_query.retrieved_chunk_ids == [chunk.id]


# ─── AC-2 · tenant_id required ────────────────────────────────────────


async def test_knowledge_ac_02_tenant_id_required(
    db_session: AsyncSession,
) -> None:
    doc = KnowledgeDocument(
        # no tenant_id
        scope=KnowledgeScope.FUNDING,
        source_type="pdf",
        source_uri="file:///tmp/x.pdf",
        title="orphan",
        sha256=_fake_sha256("z"),
        byte_size=1,
    )
    db_session.add(doc)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()


# ─── AC-3 · sha256 idempotency (partial unique) ───────────────────────


async def test_knowledge_ac_03_sha256_unique_per_tenant(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sha = _fake_sha256("a")
    await _make_document(db_session, seed_tenant, sha256=sha)
    # Second insert same (tenant, sha256) → unique violation.
    dup = KnowledgeDocument(
        tenant_id=seed_tenant.id,
        scope=KnowledgeScope.FUNDING,
        source_type="pdf",
        source_uri="file:///tmp/dup.pdf",
        title="dup",
        sha256=sha,
        byte_size=999,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ─── AC-4 · soft-delete releases sha256 slot ─────────────────────────


async def test_knowledge_ac_04_soft_delete_allows_reingest(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sha = _fake_sha256("b")
    first = await _make_document(db_session, seed_tenant, sha256=sha)
    # Soft-delete the first row.
    first.deleted_at = datetime.now(UTC)
    await db_session.flush()

    # Same (tenant, sha256) now allowed because partial unique excludes
    # soft-deleted rows.
    second = KnowledgeDocument(
        tenant_id=seed_tenant.id,
        scope=KnowledgeScope.FUNDING,
        source_type="pdf",
        source_uri="file:///tmp/re.pdf",
        title="re-ingest",
        sha256=sha,
        byte_size=2048,
    )
    db_session.add(second)
    await db_session.flush()

    assert second.id != first.id


# ─── AC-5 · chunks cascade with document ─────────────────────────────


async def test_knowledge_ac_05_chunks_cascade_on_doc_delete(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    doc = await _make_document(db_session, seed_tenant)
    await _make_chunk(db_session, seed_tenant, doc, idx=0)
    await _make_chunk(db_session, seed_tenant, doc, idx=1)

    await db_session.delete(doc)
    await db_session.flush()

    remaining = await db_session.execute(
        select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.document_id == doc.id
        )
    )
    assert remaining.scalar_one() == 0


# ─── AC-6 · chunk_idx unique within document ─────────────────────────


async def test_knowledge_ac_06_chunk_idx_unique_within_doc(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    doc = await _make_document(db_session, seed_tenant)
    await _make_chunk(db_session, seed_tenant, doc, idx=0)
    dup = KnowledgeChunk(
        tenant_id=seed_tenant.id,
        document_id=doc.id,
        chunk_idx=0,  # same idx same doc
        text="different text",
        text_sha256=_fake_sha256("dup"),
        token_count=10,
        char_count=14,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ─── AC-7 · text_sha256 unique per tenant ────────────────────────────


async def test_knowledge_ac_07_text_sha_unique_per_tenant(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    doc = await _make_document(db_session, seed_tenant)
    tsha = _fake_sha256("shared")
    await _make_chunk(
        db_session, seed_tenant, doc, idx=0, text_sha256=tsha
    )
    # Different doc, same tenant, same text_sha256 → violation.
    doc2 = await _make_document(
        db_session, seed_tenant, sha256=_fake_sha256("y"), title="second"
    )
    dup = KnowledgeChunk(
        tenant_id=seed_tenant.id,
        document_id=doc2.id,
        chunk_idx=0,
        text="dedup",
        text_sha256=tsha,
        token_count=3,
        char_count=5,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ─── AC-8 · KnowledgeScope enum rejects unknown values ───────────────


async def test_knowledge_ac_08_scope_enum_closed_set(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    # Raw insert bypassing the ORM enum; DB / SQLA still enforce the closed set.
    stmt = text(
        "INSERT INTO knowledge_documents "
        "(id, tenant_id, scope, source_type, source_uri, title, "
        " sha256, byte_size) "
        "VALUES (gen_random_uuid(), :tid, 'garbage', 'pdf', 'file:///x', "
        " 't', :sha, 1)"
    )
    with pytest.raises((DBAPIError, IntegrityError, LookupError, ValueError)):
        await db_session.execute(
            stmt, {"tid": seed_tenant.id, "sha": _fake_sha256("g")}
        )
        await db_session.flush()


# ─── AC-9 · knowledge_queries UPDATE is a silent no-op ───────────────


async def test_knowledge_ac_09_queries_update_is_noop(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    q = KnowledgeQuery(
        tenant_id=seed_tenant.id,
        actor_kind="claude",
        query_text="original",
    )
    db_session.add(q)
    await db_session.flush()
    original_id = q.id

    # Attempt a raw UPDATE — the INSTEAD NOTHING rule should silently drop it.
    result = await db_session.execute(
        update(KnowledgeQuery)
        .where(KnowledgeQuery.id == original_id)
        .values(query_text="mutated")
    )
    # rowcount is 0 because the rule dropped the write.
    assert result.rowcount == 0

    reloaded = await db_session.execute(
        select(KnowledgeQuery.query_text).where(KnowledgeQuery.id == original_id)
    )
    assert reloaded.scalar_one() == "original"


# ─── AC-10 · knowledge_queries DELETE is a silent no-op ──────────────


async def test_knowledge_ac_10_queries_delete_is_noop(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    q = KnowledgeQuery(
        tenant_id=seed_tenant.id,
        actor_kind="cron",
        actor_id="daily_brief.today_top5",
        query_text="brief scan",
    )
    db_session.add(q)
    await db_session.flush()
    qid = q.id

    result = await db_session.execute(
        text("DELETE FROM knowledge_queries WHERE id = :i"), {"i": qid}
    )
    assert result.rowcount == 0

    row = await db_session.execute(
        select(func.count()).select_from(KnowledgeQuery).where(
            KnowledgeQuery.id == qid
        )
    )
    assert row.scalar_one() == 1


# ─── AC-11 · retrieved arrays must be same length ────────────────────


async def test_knowledge_ac_11_retrieved_arrays_aligned(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    misaligned = KnowledgeQuery(
        tenant_id=seed_tenant.id,
        actor_kind="claude",
        query_text="length mismatch",
        retrieved_chunk_ids=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()],
        retrieved_scores=[0.9],  # only 1 score for 3 ids
    )
    db_session.add(misaligned)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ─── AC-12 · embeddings reuse via polymorphic pointer ────────────────


async def test_knowledge_ac_12_embeddings_polymorphic_reuse(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    doc = await _make_document(db_session, seed_tenant)
    chunk = await _make_chunk(db_session, seed_tenant, doc)

    # Store a vector for this chunk through the existing embeddings table.
    vector_dim = 1536
    emb = Embedding(
        tenant_id=seed_tenant.id,
        entity_type="knowledge_chunk",
        entity_id=chunk.id,
        vector=[0.0] * vector_dim,
        model_name="voyage-3-large",
        model_version="v1",
        source_text=chunk.text,
    )
    db_session.add(emb)
    await db_session.flush()

    hit = await db_session.execute(
        select(Embedding).where(
            Embedding.entity_type == "knowledge_chunk",
            Embedding.entity_id == chunk.id,
        )
    )
    row = hit.scalar_one()
    assert row.model_name == "voyage-3-large"
    # And knowledge_chunks itself has NO vector column — verify that.
    assert "vector" not in {c.name for c in KnowledgeChunk.__table__.columns}


# ─── AC-13 · was_blocked without reason → CHECK violation ────────────


async def test_knowledge_ac_13_blocked_requires_reason(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    bad = KnowledgeQuery(
        tenant_id=seed_tenant.id,
        actor_kind="claude",
        query_text="what",
        was_blocked=True,
        # blocked_reason left NULL
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ─── AC-14 · updated_at bumps on document update ─────────────────────


async def test_knowledge_ac_14_updated_at_bumps(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    doc = await _make_document(db_session, seed_tenant)
    original_updated = doc.updated_at
    assert original_updated is not None

    doc.title = "BP V8.2"
    await db_session.flush()
    await db_session.refresh(doc)

    assert doc.updated_at is not None
    assert doc.updated_at >= original_updated
