"""One-shot knowledge ingestion from the command line.

Usage:

    .venv/bin/python scripts/ingest_knowledge.py \
        --file ~/Drive/BUFF_OS/01_funding/2026-06-27_BP_V8.1.md \
        --scope funding \
        --tags BP,2026Q2

Markdown / plain-text files are ingested as-is. PDF / docx / pptx / xlsx
go through markitdown (must be installed / uv-cached).

The default tenant (RESTO_DEFAULT_TENANT_ID) is created on first use so a
fresh dev database works without a seed step.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
import uuid
from pathlib import Path

# Allow running from repo root without installing scripts as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.config import get_settings
from restaurant_api.database import get_sessionmaker
from restaurant_api.models import Tenant
from restaurant_api.services.knowledge_ingestion import (
    FakeExtractor,
    IngestionSource,
    ingest_source,
)
from restaurant_api.services.knowledge_ingestion.embed_backends import (
    make_embedding_client_from_settings,
)
from restaurant_api.services.knowledge_ingestion.extract import (
    MarkitdownExtractor,
)

_TEXT_SUFFIXES = {".md", ".markdown", ".txt"}


async def _ensure_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Create the default tenant row if it doesn't exist yet (fresh dev DB)."""
    existing = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(Tenant(id=tenant_id, name="BUFF OS Default", slug="buff-os-default"))
        await session.flush()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest one file into BUFF OS")
    parser.add_argument("--file", required=True, help="path to the source file")
    parser.add_argument(
        "--scope",
        required=True,
        choices=[
            "funding",
            "engineering",
            "sop",
            "brand",
            "compete",
            "subsidy",
            "system",
            "general",
        ],
    )
    parser.add_argument("--tags", default="", help="comma-separated tags")
    parser.add_argument("--title", default=None)
    parser.add_argument("--sensitive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.file).expanduser()
    if not path.is_file():
        print(f"❌ 找不到檔案: {path}")
        return 1

    settings = get_settings()
    embed = make_embedding_client_from_settings()
    if embed is None:
        print(
            "❌ Embedding backend 沒有 credentials。\n"
            f"   目前 RESTO_EMBED_BACKEND={settings.embed_backend}；"
            "請在 .env 補上對應 API key，或改用 RESTO_EMBED_BACKEND=local。"
        )
        return 1

    extractor = (
        FakeExtractor()
        if path.suffix.lower() in _TEXT_SUFFIXES
        else MarkitdownExtractor()
    )
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    tenant_id = uuid.UUID(settings.default_tenant_id)

    src = IngestionSource(
        tenant_id=tenant_id,
        scope=args.scope,
        inline_bytes_b64=base64.b64encode(path.read_bytes()).decode(),
        title=args.title or path.name,
        tags=tags,
        explicit_sensitive=args.sensitive,
        dry_run=args.dry_run,
    )

    Session = get_sessionmaker()
    async with Session() as session:
        await _ensure_tenant(session, tenant_id)
        result = await ingest_source(
            src,
            session=session,
            embedding_client=embed,
            extractor=extractor,
        )
        await session.commit()

    if result.is_duplicate:
        print(f"↩︎  已存在（同內容）：document_id={result.document_id}")
    else:
        print(
            f"✅ ingested: document_id={result.document_id}\n"
            f"   chunks={result.chunk_count}  embeddings={result.embedding_count}"
            f"  sensitive={result.is_sensitive}  cost=${result.cost_usd}"
        )
        if result.redactions:
            hits = ", ".join(f"{h.kind}×{h.count}" for h in result.redactions)
            print(f"   ⚠️  redacted: {hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
