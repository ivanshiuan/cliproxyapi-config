# knowledge_ingestion — quickstart

One-shot: bytes / file / url / drive → chunks + embeddings in DB.

```python
import base64
from restaurant_api.database import get_sessionmaker
from restaurant_api.services.knowledge_ingestion import (
    IngestionSource,
    ingest_source,
    FakeEmbeddingClient,   # swap for real client in production
    FakeExtractor,         # or MarkitdownExtractor for PDFs / docx / xlsx
)

async def run():
    body = b"# 周霸虎 BP V8.1\n\n投資人 Q&A 段落一…"
    src = IngestionSource(
        tenant_id=tenant.id,
        scope="funding",
        inline_bytes_b64=base64.b64encode(body).decode(),
        tags=["BP", "2026Q2"],
    )
    Session = get_sessionmaker()
    async with Session() as session:
        result = await ingest_source(
            src,
            session=session,
            embedding_client=FakeEmbeddingClient(vector_dim=1536),
            extractor=FakeExtractor(),
        )
        await session.commit()
    print(result.document_id, result.chunk_count, result.cost_usd)
```

Discipline:
- `is_sensitive=True` docs still land in DB — retrieval enforces the block.
- Duplicate `(tenant_id, sha256)` is idempotent: zero LLM cost on replay.
- Chunk vectors live in the shared `embeddings` table, not here.
