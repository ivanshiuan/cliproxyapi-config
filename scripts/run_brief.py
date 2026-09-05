"""Manually fire one BUFF OS brief from the command line.

Usage:

    .venv/bin/python scripts/run_brief.py today_top5
    .venv/bin/python scripts/run_brief.py engineering_gaps
    .venv/bin/python scripts/run_brief.py investor_qa_prep
    .venv/bin/python scripts/run_brief.py weekly_review

Uses the real Anthropic LLM + the embedding backend configured in .env.
Requires ANTHROPIC_API_KEY and (for cloud embed backends) the matching
embedding key. Writes the brief to the repo-root markdown file the cron
would use (MORNING_BRIEF.md etc.) and records a brief_runs row —
so a later cron run on the same day is a no-op (idempotent).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.config import get_settings
from restaurant_api.database import get_sessionmaker
from restaurant_api.jobs.daily_brief import _dispatch
from restaurant_api.models import Tenant
from restaurant_api.services.knowledge_ingestion.embed_backends import (
    make_embedding_client_from_settings,
)
from restaurant_api.services.knowledge_retrieval import (
    build_retrieval_client_from_settings,
)
from restaurant_api.services.llm_client import (
    build_llm_client_from_settings,
)

KINDS = ("today_top5", "engineering_gaps", "investor_qa_prep", "weekly_review")


async def _ensure_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    existing = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(Tenant(id=tenant_id, name="BUFF OS Default", slug="buff-os-default"))
        await session.flush()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run one BUFF OS brief now")
    parser.add_argument("kind", choices=KINDS)
    args = parser.parse_args()

    settings = get_settings()

    llm = build_llm_client_from_settings()
    if llm is None:
        print("❌ ANTHROPIC_API_KEY 沒設定（.env）。")
        return 1

    embed = make_embedding_client_from_settings()
    if embed is None:
        print(
            f"❌ Embedding backend ({settings.embed_backend}) 沒有 credentials。"
            "請補 .env 或改 RESTO_EMBED_BACKEND=local。"
        )
        return 1
    retrieval = build_retrieval_client_from_settings(embedding_client=embed)

    tenant_id = uuid.UUID(settings.default_tenant_id)
    Session = get_sessionmaker()
    async with Session() as session:
        await _ensure_tenant(session, tenant_id)
        result = await _dispatch(
            args.kind,
            session=session,
            now=None,
            sink=None,  # default FileSink → repo-root markdown
            retrieval=retrieval,
            llm=llm,
            tenant_id=tenant_id,
        )
        await session.commit()

    print(f"status: {result.status}")
    print(f"business_date: {result.business_date}")
    print(f"chunks retrieved: {result.retrieved_chunk_count}")
    if result.output_path:
        print(f"brief written to: {result.output_path}")
    if result.status == "skipped_empty_kb":
        print("💡 知識庫還沒資料 — 先跑 scripts/ingest_knowledge.py 灌幾份文件。")
    if result.error_message:
        print(f"error: {result.error_message}")
    return 0 if result.status in {"completed", "skipped_duplicate", "skipped_empty_kb"} else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
