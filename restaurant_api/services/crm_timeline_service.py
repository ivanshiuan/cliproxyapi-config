"""Unified customer timeline (對標 Twenty CRM's record timeline).

Not a table — merged at read time from seven sources, all scoped to one
customer under the request tenant:

============  =====================  ==================================  ==========
type          time column            title / detail                      amount
============  =====================  ==================================  ==========
order         orders.opened_at       "訂單 <order_no> (<status>)"        sum(line_total)
points        ledger.created_at      "點數 +N" / reason (+ note)         delta
stored_value  ledger.created_at      "儲值 +N" / reason (+ note)         delta
referral      referrals.created_at   role + status                       None
referral      referrals.qualified_at "推薦達標" (only if qualified)        reward pts*
note          created_at             "備註" / body                       None
task          created_at             task title / status + due_at        None
interaction   occurred_at            channel·direction / summary         None
============  =====================  ==================================  ==========

\\* reward points only on the referrer's side; the referred customer's
qualified event carries no amount.

**Referral decision:** a referral produces one event at ``created_at``
(signup attribution) and — once qualified — a *second* event at
``qualified_at``. Both carry the referral's id as ``ref_id``; this keeps
each event's timestamp truthful instead of overloading one row.

Pagination: ``before`` is an exclusive cursor on ``occurred_at``. Each
source fetches at most ``limit`` rows older than the cursor (index-friendly,
never a full scan), the union is sorted desc and truncated to ``limit``.
``next_before`` is the last returned event's time when the page is full,
else ``None``. Known limitation: events sharing *exactly* the cursor
timestamp across a page boundary are skipped (microsecond timestamps make
this rare in practice).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    CustomerInteraction,
    CustomerNote,
    CustomerPointsLedger,
    CustomerStoredValueLedger,
    CustomerTask,
    Order,
    OrderLine,
    Referral,
    ReferralStatus,
)
from ..schemas.crm import TPE, TimelineEvent, TimelineResponse
from .crm_service import load_customer

_TYPE_ORDER = {
    "order": 0,
    "points": 1,
    "stored_value": 2,
    "referral": 3,
    "note": 4,
    "task": 5,
    "interaction": 6,
}


def _signed(d: Decimal) -> str:
    """'+10' / '-25.5' — trailing zeros stripped for display only."""
    q = d.normalize()
    if q == q.to_integral_value():
        q = q.quantize(Decimal(1))
    return f"{q:+f}"


def _with_note(reason: str, note: str | None) -> str:
    return f"{reason} — {note}" if note else reason


async def _orders(
    session: AsyncSession, cid: uuid.UUID, tid: uuid.UUID, before: datetime | None, limit: int
) -> list[TimelineEvent]:
    total = (
        select(func.coalesce(func.sum(OrderLine.line_total), Decimal("0")))
        .where(OrderLine.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )
    stmt = select(Order, total.label("amount")).where(
        Order.customer_id == cid,
        Order.tenant_id == tid,
        Order.deleted_at.is_(None),
    )
    if before is not None:
        stmt = stmt.where(Order.opened_at < before)
    stmt = stmt.order_by(Order.opened_at.desc(), Order.id.desc()).limit(limit)
    out: list[TimelineEvent] = []
    for order, amount in (await session.execute(stmt)).all():
        out.append(
            TimelineEvent(
                type="order",
                occurred_at=order.opened_at,
                title=f"訂單 {order.order_no} ({order.status.value})",
                detail=f"service_type={order.service_type.value}",
                ref_id=order.id,
                amount=Decimal(amount),
            )
        )
    return out


async def _points(
    session: AsyncSession, cid: uuid.UUID, tid: uuid.UUID, before: datetime | None, limit: int
) -> list[TimelineEvent]:
    L = CustomerPointsLedger
    stmt = select(L).where(L.customer_id == cid, L.tenant_id == tid)
    if before is not None:
        stmt = stmt.where(L.created_at < before)
    rows = (
        await session.execute(stmt.order_by(L.created_at.desc(), L.id.desc()).limit(limit))
    ).scalars()
    return [
        TimelineEvent(
            type="points",
            occurred_at=r.created_at,
            title=f"點數 {_signed(r.delta)}",
            detail=_with_note(r.reason, r.note),
            ref_id=r.id,
            amount=r.delta,
        )
        for r in rows
    ]


async def _stored_value(
    session: AsyncSession, cid: uuid.UUID, tid: uuid.UUID, before: datetime | None, limit: int
) -> list[TimelineEvent]:
    L = CustomerStoredValueLedger
    stmt = select(L).where(L.customer_id == cid, L.tenant_id == tid)
    if before is not None:
        stmt = stmt.where(L.created_at < before)
    rows = (
        await session.execute(stmt.order_by(L.created_at.desc(), L.id.desc()).limit(limit))
    ).scalars()
    return [
        TimelineEvent(
            type="stored_value",
            occurred_at=r.created_at,
            title=f"儲值 {_signed(r.delta)}",
            detail=_with_note(r.reason, r.note),
            ref_id=r.id,
            amount=r.delta,
        )
        for r in rows
    ]


async def _referrals(
    session: AsyncSession, cid: uuid.UUID, tid: uuid.UUID, before: datetime | None, limit: int
) -> list[TimelineEvent]:
    base = select(Referral).where(
        Referral.tenant_id == tid,
        or_(Referral.referrer_id == cid, Referral.referred_id == cid),
    )
    created_stmt = base
    qualified_stmt = base.where(Referral.qualified_at.is_not(None))
    if before is not None:
        created_stmt = created_stmt.where(Referral.created_at < before)
        qualified_stmt = qualified_stmt.where(Referral.qualified_at < before)
    created_rows = (
        await session.execute(
            created_stmt.order_by(Referral.created_at.desc(), Referral.id.desc()).limit(limit)
        )
    ).scalars().all()
    qualified_rows = (
        await session.execute(
            qualified_stmt.order_by(Referral.qualified_at.desc(), Referral.id.desc()).limit(limit)
        )
    ).scalars().all()

    out: list[TimelineEvent] = []
    for r in created_rows:
        is_referrer = r.referrer_id == cid
        other = r.referred_id if is_referrer else r.referrer_id
        out.append(
            TimelineEvent(
                type="referral",
                occurred_at=r.created_at,
                title="推薦了新會員" if is_referrer else "經推薦加入",
                detail=f"status={r.status.value}; other_customer_id={other}",
                ref_id=r.id,
            )
        )
    for r in qualified_rows:
        assert r.qualified_at is not None  # filtered in SQL
        is_referrer = r.referrer_id == cid
        out.append(
            TimelineEvent(
                type="referral",
                occurred_at=r.qualified_at,
                title="推薦達標" if is_referrer else "首筆消費達成推薦資格",
                detail=f"status={ReferralStatus.QUALIFIED.value}",
                ref_id=r.id,
                amount=r.referrer_reward_points if is_referrer else None,
            )
        )
    return out


async def _notes(
    session: AsyncSession, cid: uuid.UUID, tid: uuid.UUID, before: datetime | None, limit: int
) -> list[TimelineEvent]:
    N = CustomerNote
    stmt = select(N).where(N.customer_id == cid, N.tenant_id == tid, N.deleted_at.is_(None))
    if before is not None:
        stmt = stmt.where(N.created_at < before)
    rows = (
        await session.execute(stmt.order_by(N.created_at.desc(), N.id.desc()).limit(limit))
    ).scalars()
    return [
        TimelineEvent(type="note", occurred_at=r.created_at, title="備註", detail=r.body, ref_id=r.id)
        for r in rows
    ]


async def _tasks(
    session: AsyncSession, cid: uuid.UUID, tid: uuid.UUID, before: datetime | None, limit: int
) -> list[TimelineEvent]:
    T = CustomerTask
    stmt = select(T).where(T.customer_id == cid, T.tenant_id == tid, T.deleted_at.is_(None))
    if before is not None:
        stmt = stmt.where(T.created_at < before)
    rows = (
        await session.execute(stmt.order_by(T.created_at.desc(), T.id.desc()).limit(limit))
    ).scalars()
    out: list[TimelineEvent] = []
    for r in rows:
        due_txt = r.due_at.astimezone(TPE).isoformat() if r.due_at is not None else "-"
        out.append(
            TimelineEvent(
                type="task",
                occurred_at=r.created_at,
                title=r.title,
                detail=f"status={r.status.value}; due_at={due_txt}",
                ref_id=r.id,
            )
        )
    return out


async def _interactions(
    session: AsyncSession, cid: uuid.UUID, tid: uuid.UUID, before: datetime | None, limit: int
) -> list[TimelineEvent]:
    i = CustomerInteraction
    stmt = select(i).where(i.customer_id == cid, i.tenant_id == tid)
    if before is not None:
        stmt = stmt.where(i.occurred_at < before)
    rows = (
        await session.execute(stmt.order_by(i.occurred_at.desc(), i.id.desc()).limit(limit))
    ).scalars()
    return [
        TimelineEvent(
            type="interaction",
            occurred_at=r.occurred_at,
            title=f"{r.channel.value}·{r.direction.value}",
            detail=r.summary,
            ref_id=r.id,
        )
        for r in rows
    ]


async def get_timeline(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    before: datetime | None = None,
    limit: int = 50,
) -> TimelineResponse:
    """Merged, newest-first timeline page for one customer (404 if unknown)."""
    await load_customer(session, customer_id, tenant_id=tenant_id)

    events: list[TimelineEvent] = []
    for source in (_orders, _points, _stored_value, _referrals, _notes, _tasks, _interactions):
        events.extend(await source(session, customer_id, tenant_id, before, limit))

    # Desc by time; ties broken deterministically by (type, ref_id).
    events.sort(
        key=lambda e: (e.occurred_at, -_TYPE_ORDER[e.type], e.ref_id.int),
        reverse=True,
    )
    page = events[:limit]
    next_before = page[-1].occurred_at if len(page) == limit else None
    return TimelineResponse(events=page, next_before=next_before)


__all__ = ["get_timeline"]
