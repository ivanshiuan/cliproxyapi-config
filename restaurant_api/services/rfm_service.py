"""RFM 分眾 — Recency / Frequency / Monetary segmentation + 分眾推播.

Turns the flat member list into actionable segments so the operator can target
the right LINE message at the right cohort (win back the slipping regulars,
welcome the new faces, reward the champions).

Scoring (fixed thresholds — deterministic and operator-legible; tertile scoring
can replace these once a tenant has enough volume):
- **Recency** = days since last CLOSED order: <=14 -> 3, <=45 -> 2, else 1
- **Frequency** = lifetime CLOSED order count: >=10 -> 3, >=4 -> 2, else 1
- **Monetary** = cached ``lifetime_value``: >=10000 -> 3, >=3000 -> 2, else 1

Segment decision tree (one segment per member, mutually exclusive):
- 0 orders                         -> NEW (registered, not yet transacted)
- recency score 1 (gone quiet):
    - was valuable (F>=2 or M>=2)   -> AT_RISK
    - otherwise                     -> DORMANT
- recent + F=3 + M=3               -> CHAMPION
- recent + F>=2                    -> LOYAL
- otherwise                        -> POTENTIAL

Pure reads; the caller owns the transaction. ``broadcast_to_segment`` is the one
side-effecting entry (fires a LINE broadcast to the segment's reachable members).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.line import (
    BroadcastAudience,
    LineMessage,
    LineMessenger,
    get_messenger,
)
from ..models import Customer, Order, OrderStatus
from ..schemas.rfm import (
    BroadcastResult,
    RfmSegment,
    SegmentBucket,
    SegmentsResponse,
)


def _score_recency(days: int) -> int:
    if days <= 14:
        return 3
    if days <= 45:
        return 2
    return 1


def _score_frequency(count: int) -> int:
    if count >= 10:
        return 3
    if count >= 4:
        return 2
    return 1


def _score_monetary(value: Decimal) -> int:
    if value >= Decimal("10000"):
        return 3
    if value >= Decimal("3000"):
        return 2
    return 1


def classify(
    *, recency_days: int | None, frequency: int, monetary: Decimal
) -> RfmSegment:
    """Map one member's R/F/M facts to a single segment."""
    if frequency == 0 or recency_days is None:
        return RfmSegment.NEW
    r = _score_recency(recency_days)
    f = _score_frequency(frequency)
    m = _score_monetary(monetary)
    if r == 1:
        return RfmSegment.AT_RISK if (f >= 2 or m >= 2) else RfmSegment.DORMANT
    if f == 3 and m == 3:
        return RfmSegment.CHAMPION
    if f >= 2:
        return RfmSegment.LOYAL
    return RfmSegment.POTENTIAL


async def _scored_members(
    session: AsyncSession, *, tenant_id: uuid.UUID, as_of: date
) -> list[tuple[Customer, RfmSegment]]:
    """All live members paired with their computed segment."""
    stmt = (
        select(
            Customer,
            func.count(Order.id),
            func.max(Order.business_date),
        )
        .outerjoin(
            Order,
            and_(
                Order.customer_id == Customer.id,
                Order.status == OrderStatus.CLOSED,
                Order.deleted_at.is_(None),
                # Bound to the as-of date so a (test or back-dated) future order
                # can't yield negative recency or inflate frequency. In the
                # JOIN — not WHERE — so members with no qualifying order still
                # appear (as NEW) instead of being filtered out.
                Order.business_date <= as_of,
            ),
        )
        .where(Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None))
        .group_by(Customer.id)
    )
    out: list[tuple[Customer, RfmSegment]] = []
    for customer, freq, last_date in (await session.execute(stmt)).all():
        recency = (as_of - last_date).days if last_date is not None else None
        segment = classify(
            recency_days=recency,
            frequency=freq,
            monetary=customer.lifetime_value or Decimal("0"),
        )
        out.append((customer, segment))
    return out


async def segment_counts(
    session: AsyncSession, *, tenant_id: uuid.UUID, as_of: date | None = None
) -> SegmentsResponse:
    """Member counts per RFM segment (every segment present, 0 when empty)."""
    today = as_of or date.today()
    scored = await _scored_members(session, tenant_id=tenant_id, as_of=today)
    counts = dict.fromkeys(RfmSegment, 0)
    for _, seg in scored:
        counts[seg] += 1
    return SegmentsResponse(
        total_scored=len(scored),
        segments=[SegmentBucket(segment=s, count=counts[s]) for s in RfmSegment],
    )


async def broadcast_to_segment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    segment: RfmSegment,
    message: str,
    as_of: date | None = None,
    messenger: LineMessenger | None = None,
) -> BroadcastResult:
    """Fire a LINE broadcast to a segment's LINE-reachable members."""
    today = as_of or date.today()
    scored = await _scored_members(session, tenant_id=tenant_id, as_of=today)
    recipients = tuple(
        c.line_user_id
        for c, seg in scored
        if seg == segment and c.line_user_id
    )
    if not recipients:
        return BroadcastResult(segment=segment, audience_size=0, delivered=0)
    out = messenger or get_messenger()
    delivered = await out.broadcast(
        BroadcastAudience(explicit_user_ids=recipients),
        LineMessage(kind="text", text=message),
    )
    return BroadcastResult(
        segment=segment,
        audience_size=len(recipients),
        delivered=delivered,
    )


__all__ = [
    "RfmSegment",
    "broadcast_to_segment",
    "classify",
    "segment_counts",
]
