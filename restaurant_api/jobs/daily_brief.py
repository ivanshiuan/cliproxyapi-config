"""4 scheduled brief jobs — the heart of BUFF OS's daily rhythm.

Every brief follows the same skeleton:

    1. Compute the Asia/Taipei ``business_date`` for the run.
    2. Look up the versioned prompt template.
    3. Check ``brief_runs`` for an existing row on
       ``(tenant, kind, business_date, prompt_template_version)``.
       Completed → short-circuit ``skipped_duplicate``.
       Failed → retry: UPDATE the same row.
    4. Retrieve top-k chunks from the knowledge base via
       ``RetrievalClient`` (scope filter varies per brief).
    5. If retrieval is empty → ``skipped_empty_kb`` with a graceful
       "please ingest" message. **LLM is not called.**
    6. Otherwise call ``LLMClient.complete()`` with the rendered prompt.
    7. Write the brief markdown to the ``BriefSink``.
    8. Insert a ``knowledge_queries`` audit row and a ``brief.completed``
       ``audit_log`` row.

Sinks are file / null in this slice — Notion / Drive / Email arrive in a
later spec, per the "proposal, not action" rule from CLAUDE.md.

See ``specs/daily_brief_cron.md`` for the full 17-AC contract.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_sessionmaker
from ..models import BriefRun, KnowledgeQuery
from ..services.audit_service import audit
from ._brief_prompts import (
    SYSTEM_ROLE,
    render,
    resolve_template,
    scan_forbidden,
)
from ._brief_sinks import BriefSink, FileSink, NullSink

logger = logging.getLogger("restaurant_api.jobs.daily_brief")

BriefKind = Literal[
    "today_top5",
    "engineering_gaps",
    "investor_qa_prep",
    "weekly_review",
]

BriefStatus = Literal[
    "completed",
    "skipped_duplicate",
    "skipped_empty_kb",
    "failed",
]


# ─── config table per brief kind (scope filter + k + default output path) ──

_BRIEF_CONFIG: dict[BriefKind, dict[str, object]] = {
    "today_top5": {
        "scopes": ["funding", "engineering", "sop", "subsidy"],
        "k_per_scope": 15,
        "output": "MORNING_BRIEF.md",
    },
    "engineering_gaps": {
        "scopes": ["engineering"],
        "k_per_scope": 40,
        "output": "ENGINEERING_GAPS.md",
    },
    "investor_qa_prep": {
        "scopes": ["funding"],
        "k_per_scope": 50,
        "output": "INVESTOR_QA_PREP.md",
    },
    "weekly_review": {
        "scopes": [
            "funding",
            "engineering",
            "sop",
            "brand",
            "compete",
            "subsidy",
            "system",
        ],
        "k_per_scope": 10,
        "output": "WEEKLY_REVIEW.md",
    },
}


# ─── domain types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieval hit — id + text + score + doc_id + section_path."""

    chunk_id: UUID
    document_id: UUID
    text: str
    score: float
    section_path: str | None = None


@dataclass(frozen=True)
class LLMResult:
    """One completion round-trip."""

    text: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class LLMClient(Protocol):
    """The shape any LLM backend must satisfy."""

    async def complete(
        self, *, system: str, user: str, model: str | None = None
    ) -> LLMResult:
        ...  # pragma: no cover


class RetrievalClient(Protocol):
    """The shape the retrieval service must satisfy.

    ``retrieve`` returns top-k chunks for the given scopes + query. Empty
    list means the knowledge base for those scopes is empty (or the tenant
    hasn't ingested anything yet).
    """

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        scopes: list[str],
        query: str,
        k: int,
    ) -> list[RetrievedChunk]:
        ...  # pragma: no cover


# ─── Fake clients for tests (no external network) ──────────────────────


@dataclass
class FakeLLMClient:
    reply: str = "# BRIEF\n\nTL;DR: ok"
    model: str = "fake-model"
    prompt_tokens: int = 100
    completion_tokens: int = 200
    fail_on_call: int | None = None  # 1 = fail on the first call, etc.

    calls: list[dict[str, str]] = field(default_factory=list)
    _call_count: int = 0

    async def complete(
        self, *, system: str, user: str, model: str | None = None
    ) -> LLMResult:
        self._call_count += 1
        self.calls.append({"system": system, "user": user, "model": model or ""})
        if (
            self.fail_on_call is not None
            and self._call_count == self.fail_on_call
        ):
            raise RuntimeError("LLM boom")
        return LLMResult(
            text=self.reply,
            model_name=model or self.model,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            latency_ms=42,
        )


@dataclass
class FakeRetrievalClient:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        scopes: list[str],
        query: str,
        k: int,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "scopes": list(scopes),
                "query": query,
                "k": k,
            }
        )
        return list(self.chunks[:k])


# ─── result envelope ───────────────────────────────────────────────────


class BriefRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    brief_kind: BriefKind
    tenant_id: UUID
    business_date: date
    status: BriefStatus
    brief_run_id: UUID | None
    retrieved_chunk_count: int
    prompt_template_version: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    output_path: str | None
    error_message: str | None


# ─── business_date helpers ─────────────────────────────────────────────


def _tpe(now: datetime) -> datetime:
    return now.astimezone(ZoneInfo("Asia/Taipei"))


def _business_date(kind: BriefKind, now: datetime) -> date:
    tpe = _tpe(now)
    if kind == "weekly_review":
        # If today is Mon-Fri (weekday <= 4), that's the running-week
        # Friday's calendar row; if the job misfires on Sat/Sun, we still
        # want the LAST Friday, not next Friday.
        d = tpe.date()
        # Move forward to the Friday of this week if we're before Fri,
        # move back to Friday if we're past it.
        weekday = d.weekday()  # Mon=0 .. Sun=6
        if weekday <= 4:
            # This week's Friday
            return d + timedelta(days=(4 - weekday))
        # Sat / Sun — last Friday
        return d - timedelta(days=(weekday - 4))
    return tpe.date()


def _week_range(anchor: date) -> tuple[date, date]:
    """Return (Mon, Fri) for the ISO week containing ``anchor``."""
    monday = anchor - timedelta(days=anchor.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


# ─── core runner ───────────────────────────────────────────────────────


async def _run_brief(
    kind: BriefKind,
    *,
    session: AsyncSession,
    tenant_id: UUID,
    llm: LLMClient,
    retrieval: RetrievalClient,
    sink: BriefSink,
    now: datetime,
) -> BriefRunResult:
    """Shared skeleton for the 4 briefs — see module docstring."""
    version_id, template = resolve_template(kind)
    biz_date = _business_date(kind, now)
    config = _BRIEF_CONFIG[kind]
    scopes = list(config["scopes"])  # type: ignore[arg-type]
    k_per_scope = int(config["k_per_scope"])  # type: ignore[arg-type]

    # 1. Idempotency: is there an existing row on this (tenant, kind,
    #    business_date, version)?
    existing_stmt = select(BriefRun).where(
        BriefRun.tenant_id == tenant_id,
        BriefRun.brief_kind == kind,
        BriefRun.business_date == biz_date,
        BriefRun.prompt_template_version == version_id,
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None and existing.status == "completed":
        logger.info(
            "daily_brief.duplicate",
            extra={
                "brief_kind": kind,
                "brief_run_id": str(existing.id),
                "business_date": biz_date.isoformat(),
            },
        )
        return BriefRunResult(
            brief_kind=kind,
            tenant_id=tenant_id,
            business_date=biz_date,
            status="skipped_duplicate",
            brief_run_id=existing.id,
            retrieved_chunk_count=existing.retrieved_chunk_count,
            prompt_template_version=version_id,
            prompt_tokens=existing.prompt_tokens,
            completion_tokens=existing.completion_tokens,
            latency_ms=existing.latency_ms,
            output_path=existing.output_path,
            error_message=None,
        )

    # 2. Retrieval — one call per brief with all its scopes together.
    query = _query_for(kind, biz_date)
    total_k = k_per_scope * max(len(scopes), 1)
    hits = await retrieval.retrieve(
        tenant_id=tenant_id,
        scopes=scopes,
        query=query,
        k=total_k,
    )

    # 3. Empty KB — write a graceful "no data" brief and STOP (no LLM cost).
    if not hits:
        placeholder = _empty_kb_markdown(kind, biz_date, scopes)
        output_path = await sink.write(kind=kind, markdown=placeholder)
        row = _upsert_brief_row(
            existing=existing,
            session=session,
            tenant_id=tenant_id,
            kind=kind,
            biz_date=biz_date,
            version_id=version_id,
            status="skipped_empty_kb",
            retrieved_chunk_count=0,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
            output_path=output_path,
            output_markdown=placeholder,
            error_message=None,
            meta={"reason": "empty_knowledge_base"},
        )
        await session.flush()
        await _write_retrieval_audit(
            session,
            tenant_id=tenant_id,
            kind=kind,
            query_text=query,
            hits=[],
            scopes=scopes,
        )
        return _envelope(row, kind, tenant_id, biz_date, version_id)

    # 4. Prompt + LLM
    corpus = _format_corpus(hits)
    audit_summary = ""  # weekly_review can enrich this via a follow-up spec
    context = _prompt_context(
        kind=kind,
        scopes=scopes,
        biz_date=biz_date,
        chunk_count=len(hits),
        corpus=corpus,
        audit_summary=audit_summary,
    )
    user_prompt = render(template, **context)

    t0 = time.monotonic()
    try:
        llm_result = await llm.complete(
            system=SYSTEM_ROLE, user=user_prompt, model=None
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        row = _upsert_brief_row(
            existing=existing,
            session=session,
            tenant_id=tenant_id,
            kind=kind,
            biz_date=biz_date,
            version_id=version_id,
            status="failed",
            retrieved_chunk_count=len(hits),
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            output_path=None,
            output_markdown=None,
            error_message=f"{type(exc).__name__}: {exc}",
            meta={},
        )
        await session.flush()
        # Retrieval still happened; log it so the ledger reflects reality.
        await _write_retrieval_audit(
            session,
            tenant_id=tenant_id,
            kind=kind,
            query_text=query,
            hits=hits,
            scopes=scopes,
        )
        return _envelope(row, kind, tenant_id, biz_date, version_id)
    latency_ms = int((time.monotonic() - t0) * 1000)

    # 5. Forbidden-word scan (warn only, do not block per spec AC-17)
    forbidden = scan_forbidden(llm_result.text)
    meta: dict[str, object] = {}
    if forbidden:
        meta["forbidden_words"] = forbidden
        logger.warning(
            "daily_brief.forbidden_words",
            extra={"brief_kind": kind, "hits": forbidden},
        )

    # 6. Sink
    output_path = await sink.write(kind=kind, markdown=llm_result.text)

    # 7. Persist row + audit
    row = _upsert_brief_row(
        existing=existing,
        session=session,
        tenant_id=tenant_id,
        kind=kind,
        biz_date=biz_date,
        version_id=version_id,
        status="completed",
        retrieved_chunk_count=len(hits),
        prompt_tokens=llm_result.prompt_tokens,
        completion_tokens=llm_result.completion_tokens,
        latency_ms=latency_ms,
        output_path=output_path,
        output_markdown=llm_result.text,
        error_message=None,
        meta=meta,
    )
    await session.flush()

    await _write_retrieval_audit(
        session,
        tenant_id=tenant_id,
        kind=kind,
        query_text=query,
        hits=hits,
        scopes=scopes,
        answer_text=llm_result.text,
        prompt_tokens=llm_result.prompt_tokens,
        completion_tokens=llm_result.completion_tokens,
        latency_ms=latency_ms,
        model_name=llm_result.model_name,
    )
    await audit(
        session,
        action="brief.completed",
        tenant_id=tenant_id,
        target=("brief_runs", row.id),
        after={
            "brief_kind": kind,
            "business_date": biz_date.isoformat(),
            "prompt_version": version_id,
            "chunk_count": len(hits),
            "output_path": output_path,
            "forbidden_words": forbidden,
        },
        reason=f"brief.{kind}",
    )
    return _envelope(row, kind, tenant_id, biz_date, version_id)


# ─── public wrappers (one per brief) ───────────────────────────────────


async def run_today_top5(
    *,
    session: AsyncSession | None = None,
    now: datetime | None = None,
    sink: BriefSink | None = None,
    retrieval: RetrievalClient | None = None,
    llm: LLMClient | None = None,
    tenant_id: UUID | None = None,
) -> BriefRunResult:
    return await _dispatch(
        "today_top5",
        session=session,
        now=now,
        sink=sink,
        retrieval=retrieval,
        llm=llm,
        tenant_id=tenant_id,
    )


async def run_engineering_gaps(
    *,
    session: AsyncSession | None = None,
    now: datetime | None = None,
    sink: BriefSink | None = None,
    retrieval: RetrievalClient | None = None,
    llm: LLMClient | None = None,
    tenant_id: UUID | None = None,
) -> BriefRunResult:
    return await _dispatch(
        "engineering_gaps",
        session=session,
        now=now,
        sink=sink,
        retrieval=retrieval,
        llm=llm,
        tenant_id=tenant_id,
    )


async def run_investor_qa_prep(
    *,
    session: AsyncSession | None = None,
    now: datetime | None = None,
    sink: BriefSink | None = None,
    retrieval: RetrievalClient | None = None,
    llm: LLMClient | None = None,
    tenant_id: UUID | None = None,
) -> BriefRunResult:
    return await _dispatch(
        "investor_qa_prep",
        session=session,
        now=now,
        sink=sink,
        retrieval=retrieval,
        llm=llm,
        tenant_id=tenant_id,
    )


async def run_weekly_review(
    *,
    session: AsyncSession | None = None,
    now: datetime | None = None,
    sink: BriefSink | None = None,
    retrieval: RetrievalClient | None = None,
    llm: LLMClient | None = None,
    tenant_id: UUID | None = None,
) -> BriefRunResult:
    return await _dispatch(
        "weekly_review",
        session=session,
        now=now,
        sink=sink,
        retrieval=retrieval,
        llm=llm,
        tenant_id=tenant_id,
    )


# ─── glue / helpers ────────────────────────────────────────────────────


async def _dispatch(
    kind: BriefKind,
    *,
    session: AsyncSession | None,
    now: datetime | None,
    sink: BriefSink | None,
    retrieval: RetrievalClient | None,
    llm: LLMClient | None,
    tenant_id: UUID | None,
) -> BriefRunResult:
    """Wire defaults and either use an injected session or open one."""
    now_ts = now or datetime.now(UTC)
    settings = get_settings()
    tid = tenant_id or uuid.UUID(settings.default_tenant_id)
    resolved_sink = sink or FileSink(str(_BRIEF_CONFIG[kind]["output"]))

    if retrieval is None or llm is None:
        raise RuntimeError(
            "retrieval and llm clients are Protocol-only in this slice; "
            "a follow-up spec wires the real defaults."
        )

    if session is not None:
        return await _run_brief(
            kind,
            session=session,
            tenant_id=tid,
            llm=llm,
            retrieval=retrieval,
            sink=resolved_sink,
            now=now_ts,
        )

    Session = get_sessionmaker()
    async with Session() as own_session:
        try:
            result = await _run_brief(
                kind,
                session=own_session,
                tenant_id=tid,
                llm=llm,
                retrieval=retrieval,
                sink=resolved_sink,
                now=now_ts,
            )
            await own_session.commit()
            return result
        except Exception:
            await own_session.rollback()
            raise


def _query_for(kind: BriefKind, biz_date: date) -> str:
    """Retrieval query per brief kind — the natural-language question the
    retriever gets scored against.
    """
    if kind == "today_top5":
        return (
            f"周霸虎 {biz_date.isoformat()} 最重要的 5 件事："
            "募資、現金流、工程、營運、品牌各面向的關鍵決策點"
        )
    if kind == "engineering_gaps":
        return (
            "工程估價、缺項、比價、廠商差異、單項價格異常、追加款風險"
        )
    if kind == "investor_qa_prep":
        return "投資人可能提問：估值、回收期、股權、單店模型、擴店節奏"
    # weekly_review
    return (
        "本週決策、進度、風險、下週 3 件最重要——依戰場分（募資、工程、"
        "營運、品牌、競品、補助、AI 系統）"
    )


def _format_corpus(hits: list[RetrievedChunk]) -> str:
    """Turn retrieval hits into a compact ID-tagged corpus block."""
    lines = []
    for h in hits:
        header = f"[chunk_id={h.chunk_id} doc={h.document_id} score={h.score:.4f}]"
        if h.section_path:
            header += f" ({h.section_path})"
        lines.append(f"{header}\n{h.text.strip()}\n")
    return "\n".join(lines)


def _prompt_context(
    *,
    kind: BriefKind,
    scopes: list[str],
    biz_date: date,
    chunk_count: int,
    corpus: str,
    audit_summary: str,
) -> dict[str, object]:
    week_start, week_end = _week_range(biz_date)
    return {
        "scope_summary": ", ".join(scopes),
        "chunk_count": chunk_count,
        "today_taipei": biz_date.isoformat(),
        "is_weekend": (
            "yes" if biz_date.weekday() >= 5 else "no"
        ),
        "week_start_taipei": week_start.isoformat(),
        "week_end_taipei": week_end.isoformat(),
        "corpus": corpus,
        "audit_summary": audit_summary or "(本週無資料)",
    }


def _empty_kb_markdown(
    kind: BriefKind, biz_date: date, scopes: list[str]
) -> str:
    return (
        f"# {kind} — {biz_date.isoformat()}\n\n"
        f"該 scope（{', '.join(scopes)}）尚無資料，請先 ingest。\n\n"
        "當你把最新 BP / 估價單 / SOP 丟進 Drive 並跑 ingestion，"
        "下一次 brief 就會有內容。\n"
    )


def _upsert_brief_row(
    *,
    existing: BriefRun | None,
    session: AsyncSession,
    tenant_id: UUID,
    kind: BriefKind,
    biz_date: date,
    version_id: str,
    status: BriefStatus,
    retrieved_chunk_count: int,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    output_path: str | None,
    output_markdown: str | None,
    error_message: str | None,
    meta: dict[str, object],
) -> BriefRun:
    """Insert new BriefRun or update existing (retry path)."""
    now_ts = datetime.now(UTC)
    if existing is not None:
        existing.status = status
        existing.retrieved_chunk_count = retrieved_chunk_count
        existing.prompt_tokens = prompt_tokens
        existing.completion_tokens = completion_tokens
        existing.latency_ms = latency_ms
        existing.output_path = output_path
        existing.output_markdown = output_markdown
        existing.error_message = error_message
        existing.meta = dict(meta)
        if status == "completed":
            existing.completed_at = now_ts
        return existing
    row = BriefRun(
        tenant_id=tenant_id,
        brief_kind=kind,
        business_date=biz_date,
        status=status,
        prompt_template_version=version_id,
        retrieved_chunk_count=retrieved_chunk_count,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        output_path=output_path,
        output_markdown=output_markdown,
        error_message=error_message,
        meta=dict(meta),
        completed_at=(now_ts if status == "completed" else None),
    )
    session.add(row)
    return row


async def _write_retrieval_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    kind: BriefKind,
    query_text: str,
    hits: list[RetrievedChunk],
    scopes: list[str],
    answer_text: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    model_name: str | None = None,
) -> KnowledgeQuery:
    """Persist a knowledge_queries row so the RAG ledger is complete."""
    from decimal import Decimal

    q = KnowledgeQuery(
        tenant_id=tenant_id,
        actor_kind="cron",
        actor_id=f"daily_brief.{kind}",
        query_text=query_text,
        scope_filter=list(scopes),
        tag_filter=[],
        retrieved_chunk_ids=[h.chunk_id for h in hits],
        retrieved_scores=[Decimal(str(round(h.score, 6))) for h in hits],
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        answer_text=answer_text,
        answer_citations={
            "chunks": [str(h.chunk_id) for h in hits],
            "documents": list({str(h.document_id) for h in hits}),
        },
    )
    session.add(q)
    await session.flush()
    return q


def _envelope(
    row: BriefRun,
    kind: BriefKind,
    tenant_id: UUID,
    biz_date: date,
    version_id: str,
) -> BriefRunResult:
    return BriefRunResult(
        brief_kind=kind,
        tenant_id=tenant_id,
        business_date=biz_date,
        status=row.status,  # type: ignore[arg-type]
        brief_run_id=row.id,
        retrieved_chunk_count=row.retrieved_chunk_count,
        prompt_template_version=version_id,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        latency_ms=row.latency_ms,
        output_path=row.output_path,
        error_message=row.error_message,
    )


# ── Scheduler wiring — appended to jobs.__init__._register ────────────

_SCHEDULE = [
    ("today_top5", {"hour": 9, "minute": 0}),
    ("engineering_gaps", {"hour": 18, "minute": 0}),
    ("investor_qa_prep", {"day_of_week": "mon", "hour": 9, "minute": 0}),
    ("weekly_review", {"day_of_week": "fri", "hour": 17, "minute": 0}),
]


__all__ = [
    "BriefKind",
    "BriefRunResult",
    "FakeLLMClient",
    "FakeRetrievalClient",
    "FileSink",
    "LLMClient",
    "LLMResult",
    "NullSink",
    "RetrievalClient",
    "RetrievedChunk",
    "run_engineering_gaps",
    "run_investor_qa_prep",
    "run_today_top5",
    "run_weekly_review",
]
