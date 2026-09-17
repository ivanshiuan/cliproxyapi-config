"""Phase 2 backend tests — LLM, embed factory, pgvector retrieval.

Everything network-mocked via ``httpx.MockTransport`` so nothing hits
Anthropic / OpenAI / Voyage in CI.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import (
    Embedding,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeScope,
    Tenant,
)
from restaurant_api.services.knowledge_ingestion.embed_backends import (
    SCHEMA_VECTOR_DIM,
    LocalHashEmbeddingClient,
    OpenAIEmbeddingClient,
    VoyageEmbeddingClient,
    make_embedding_client_from_settings,
)
from restaurant_api.services.knowledge_retrieval import (
    PgVectorRetrievalClient,
)

# ─── OpenAI embed ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_embed_happy_path() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "text-embedding-3-small"
        assert body["dimensions"] == SCHEMA_VECTOR_DIM
        assert body["input"] == ["hello", "世界"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1] * SCHEMA_VECTOR_DIM},
                    {"embedding": [0.2] * SCHEMA_VECTOR_DIM},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    embed = OpenAIEmbeddingClient(api_key="sk-fake", client=client)
    vecs = await embed.embed(["hello", "世界"], model="text-embedding-3-small")
    assert len(vecs) == 2
    assert all(len(v) == SCHEMA_VECTOR_DIM for v in vecs)
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_embed_5xx_retries_then_fails() -> None:
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    embed = OpenAIEmbeddingClient(api_key="sk-fake", client=client)
    from restaurant_api.services.knowledge_ingestion.errors import (
        IngestionEmbedError,
    )

    with pytest.raises(IngestionEmbedError):
        await embed.embed(["x"], model="text-embedding-3-small")
    # 1 initial + 3 retries = 4 total.
    assert calls["n"] == 4
    await client.aclose()


# ─── Voyage embed ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voyage_embed_matryoshka_slice() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "voyage-3-large"
        assert body["output_dimension"] == 2048
        # Return 2048-dim vectors; the client should slice to 1536.
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": list(range(2048))},  # int→float in json
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    embed = VoyageEmbeddingClient(api_key="pa-fake", client=client)
    vecs = await embed.embed(["hello"], model="voyage-3-large")
    assert len(vecs) == 1
    assert len(vecs[0]) == SCHEMA_VECTOR_DIM
    # And the slice preserves the head: first element is 0, last is 1535.
    assert vecs[0][0] == 0
    assert vecs[0][-1] == 1535
    await client.aclose()


# ─── LocalHash embed ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_hash_embed_deterministic() -> None:
    e = LocalHashEmbeddingClient()
    v1 = (await e.embed(["hello"], model="local"))[0]
    v2 = (await e.embed(["hello"], model="local"))[0]
    v3 = (await e.embed(["world"], model="local"))[0]
    assert v1 == v2
    assert v1 != v3
    assert len(v1) == SCHEMA_VECTOR_DIM
    # Range check: [-1, 1]
    assert all(-1.0 <= v <= 1.0 for v in v1)


# ─── Factory ─────────────────────────────────────────────────────────


def test_embed_factory_openai_no_key_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("RESTO_EMBED_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from restaurant_api.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    client = make_embedding_client_from_settings()
    get_settings.cache_clear()  # type: ignore[attr-defined]
    assert client is None


def test_embed_factory_local_needs_no_key(monkeypatch) -> None:
    monkeypatch.setenv("RESTO_EMBED_BACKEND", "local")
    from restaurant_api.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    client = make_embedding_client_from_settings()
    get_settings.cache_clear()  # type: ignore[attr-defined]
    assert isinstance(client, LocalHashEmbeddingClient)


def test_embed_factory_unknown_raises(monkeypatch) -> None:
    monkeypatch.setenv("RESTO_EMBED_BACKEND", "openai")  # valid, then patch value
    from restaurant_api.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    # Bypass pattern validation to prove the factory itself defends.
    settings = get_settings()
    object.__setattr__(settings, "embed_backend", "borked")
    with pytest.raises(ValueError):
        make_embedding_client_from_settings()
    get_settings.cache_clear()  # type: ignore[attr-defined]


# ─── PgVector retrieval (real DB) ────────────────────────────────────


@pytest.mark.asyncio
async def test_pgvector_retrieval_top_k(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """End-to-end: seed a doc + 3 chunks + 3 embeddings, then retrieve."""
    # Set up 3 chunks with hash-based deterministic vectors and a doc.
    doc = KnowledgeDocument(
        tenant_id=seed_tenant.id,
        scope=KnowledgeScope.FUNDING,
        source_type="markdown",
        source_uri="inline://test",
        title="BP",
        sha256="a" * 64,
        byte_size=100,
    )
    db_session.add(doc)
    await db_session.flush()

    embed = LocalHashEmbeddingClient()
    texts = ["投資人 Q&A 段落一", "工程估價 段落二", "SOP 訓練 段落三"]
    vectors = await embed.embed(texts, model="local-hash")
    for i, (t, v) in enumerate(zip(texts, vectors, strict=True)):
        c = KnowledgeChunk(
            tenant_id=seed_tenant.id,
            document_id=doc.id,
            chunk_idx=i,
            text=t,
            text_sha256=("c" + str(i)) * 32,
            token_count=10,
            char_count=len(t),
        )
        db_session.add(c)
        await db_session.flush()
        db_session.add(
            Embedding(
                tenant_id=seed_tenant.id,
                entity_type="knowledge_chunk",
                entity_id=c.id,
                vector=v,
                model_name="local-hash",
                model_version="v1",
            )
        )
        await db_session.flush()

    # Retrieve using the SAME session (shared txn so the fresh rows are
    # visible to the read query).
    retriever = PgVectorRetrievalClient(
        embedding_client=embed,
        model_name="local-hash",
        model_version="v1",
        session=db_session,
    )
    # Search using text identical to chunk 0 → chunk 0 must rank first.
    hits = await retriever.retrieve(
        tenant_id=seed_tenant.id,
        scopes=["funding"],
        query=texts[0],
        k=3,
    )
    assert len(hits) == 3
    assert hits[0].text == texts[0]
    # Score is cosine similarity = 1 for identical vectors.
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_pgvector_retrieval_scope_filter(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """A chunk under scope='sop' must NOT appear when scopes=['funding']."""
    embed = LocalHashEmbeddingClient()
    for scope, tag in ((KnowledgeScope.FUNDING, "f"), (KnowledgeScope.SOP, "s")):
        doc = KnowledgeDocument(
            tenant_id=seed_tenant.id,
            scope=scope,
            source_type="markdown",
            source_uri=f"inline://{tag}",
            title=f"doc-{tag}",
            sha256=(tag * 64)[:64],
            byte_size=100,
        )
        db_session.add(doc)
        await db_session.flush()
        c = KnowledgeChunk(
            tenant_id=seed_tenant.id,
            document_id=doc.id,
            chunk_idx=0,
            text=f"text-{tag}",
            text_sha256=(tag + "x") * 32,
            token_count=1,
            char_count=6,
        )
        db_session.add(c)
        await db_session.flush()
        vec = (await embed.embed([f"text-{tag}"], model="local-hash"))[0]
        db_session.add(
            Embedding(
                tenant_id=seed_tenant.id,
                entity_type="knowledge_chunk",
                entity_id=c.id,
                vector=vec,
                model_name="local-hash",
                model_version="v1",
            )
        )
        await db_session.flush()

    retriever = PgVectorRetrievalClient(
        embedding_client=embed,
        model_name="local-hash",
        model_version="v1",
        session=db_session,
    )
    hits = await retriever.retrieve(
        tenant_id=seed_tenant.id,
        scopes=["funding"],
        query="text-f",
        k=5,
    )
    assert len(hits) == 1
    assert hits[0].text == "text-f"


# ─── LLM client (mocked Anthropic transport) ─────────────────────────


@pytest.mark.asyncio
async def test_anthropic_llm_client_happy_path(monkeypatch) -> None:
    """Exercise the retry-free happy path through the SDK-level surface.

    We inject a stub AsyncAnthropic-shaped object so we don't need the
    real SDK to talk to anything. The client itself uses ``.messages.create``.
    """
    from restaurant_api.services.llm_client import AnthropicLLMClient

    class _FakeMessages:
        async def create(self, **kw):  # type: ignore[no-untyped-def]
            captured["kwargs"] = kw

            class _Usage:
                input_tokens = 111
                output_tokens = 222
                cache_read_input_tokens = 0

            class _Block:
                text = "# BRIEF\n\nTL;DR: ok"

            class _Resp:
                def __init__(self):
                    self.content = [_Block()]
                    self.usage = _Usage()

            return _Resp()

    class _FakeClient:
        messages = _FakeMessages()

    captured: dict = {}
    llm = AnthropicLLMClient(
        api_key="sk-ant-test-x",
        model="claude-sonnet-5",
        max_output_tokens=1024,
        prompt_cache=True,
        client=_FakeClient(),  # type: ignore[arg-type]
    )
    r = await llm.complete(system="You are a brief writer.", user="write a brief")
    assert "BRIEF" in r.text
    assert r.model_name == "claude-sonnet-5"
    assert r.prompt_tokens == 111
    assert r.completion_tokens == 222
    # Prompt cache marker present.
    system_field = captured["kwargs"]["system"]
    assert isinstance(system_field, list)
    assert system_field[0]["cache_control"]["type"] == "ephemeral"
