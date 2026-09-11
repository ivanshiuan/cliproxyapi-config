"""pgvector-backed retrieval client — the real implementation of the
``RetrievalClient`` Protocol declared in
:mod:`restaurant_api.jobs.daily_brief`.

Retrieval strategy (v1):

1. Embed the query using the *same* embedding backend that ingested the
   corpus, so cosine distance is comparable.
2. Cosine ANN search on the shared ``embeddings`` table, filtered by:
     * ``entity_type = 'knowledge_chunk'``
     * ``tenant_id`` (via join to ``knowledge_chunks``)
     * ``model_name + model_version`` (so mixing backends doesn't pollute
       the ranking)
     * ``knowledge_documents.scope IN (:scopes)``
     * ``knowledge_documents.deleted_at IS NULL``
3. Return top-k as :class:`RetrievedChunk` dataclasses.

The query runs in its OWN read transaction opened by the client — the
brief cron then writes audit + brief_runs rows in a separate transaction
it manages itself. This intentionally decouples read-only retrieval from
the transactional side effects so a large retrieval doesn't hold write
locks.

Hybrid BM25 + cosine ranking (spec §7.2) is deferred to a follow-up
slice — the ``tsvector`` GIN index isn't installed yet. When it lands,
the ``retrieve`` method grows a ``mode`` kwarg without breaking callers.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..jobs.daily_brief import RetrievedChunk
from .knowledge_ingestion.embed import EmbeddingClient

logger = logging.getLogger("restaurant_api.knowledge_retrieval")


class PgVectorRetrievalClient:
    """Real retrieval — cosine ANN via pgvector's ``<=>`` operator.

    Takes an :class:`EmbeddingClient` to embed the query, plus either a
    ``sessionmaker`` (opens a fresh read session per call) or an
    ``AsyncSession`` (reuses one caller-provided session).
    """

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        model_name: str,
        model_version: str,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        if sessionmaker is None and session is None:
            raise ValueError(
                "PgVectorRetrievalClient needs sessionmaker OR session"
            )
        self._embed = embedding_client
        self._model_name = model_name
        self._model_version = model_version
        self._sessionmaker = sessionmaker
        self._session = session

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        scopes: list[str],
        query: str,
        k: int,
    ) -> list[RetrievedChunk]:
        vectors = await self._embed.embed([query], model=self._model_name)
        if not vectors or len(vectors[0]) != self._embed.vector_dim:
            logger.warning(
                "retrieve.bad_query_vector",
                extra={"got_len": len(vectors[0]) if vectors else 0},
            )
            return []
        query_vec = vectors[0]

        # pgvector expects the vector as a bracketed string literal when
        # bound via parameter (or the pgvector-sqlalchemy adapter — but we
        # want backend-agnostic raw SQL here so the same query can be
        # explained by hand from psql). Format with :.6g precision:
        # plenty for cosine, small on wire.
        vec_literal = "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]"

        sql = sql_text(
            """
            SELECT
                c.id           AS chunk_id,
                c.document_id  AS document_id,
                c.text         AS text,
                c.section_path AS section_path,
                1 - (e.vector <=> CAST(:qvec AS vector)) AS score
            FROM embeddings e
            JOIN knowledge_chunks c ON c.id = e.entity_id
            JOIN knowledge_documents d ON d.id = c.document_id
            WHERE e.entity_type = 'knowledge_chunk'
              AND e.tenant_id    = :tid
              AND e.model_name   = :mname
              AND e.model_version = :mver
              AND d.scope        = ANY(:scopes)
              AND d.deleted_at IS NULL
              AND d.tenant_id    = :tid
            ORDER BY e.vector <=> CAST(:qvec AS vector)
            LIMIT :k
            """
        )
        params: dict[str, Any] = {
            "qvec": vec_literal,
            "tid": tenant_id,
            "mname": self._model_name,
            "mver": self._model_version,
            "scopes": list(scopes),
            "k": k,
        }

        session = self._session
        close_after = False
        if session is None:
            assert self._sessionmaker is not None
            session = self._sessionmaker()
            close_after = True

        try:
            rows = (await session.execute(sql, params)).mappings().all()
        finally:
            if close_after:
                await session.close()

        return [
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                text=row["text"],
                score=float(row["score"]),
                section_path=row["section_path"],
            )
            for row in rows
        ]


def build_retrieval_client_from_settings(
    *,
    embedding_client: EmbeddingClient,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> PgVectorRetrievalClient:
    """Convenience factory: pull model name/version from settings."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    s = get_settings()
    return PgVectorRetrievalClient(
        embedding_client=embedding_client,
        model_name=s.embed_model,
        model_version=s.embed_model_version,
        sessionmaker=sessionmaker or get_sessionmaker(),
    )


__all__ = [
    "PgVectorRetrievalClient",
    "build_retrieval_client_from_settings",
]
