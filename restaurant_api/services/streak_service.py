"""連續回訪 Streak — consecutive visit-day multiplier on points accrual.

Behavioural-economics lever: a visible "你已連續回訪 N 天,點數加碼!" streak turns
one-off diners into habit loops. The streak is **computed** from order history
(no stored counter to drift) — the count of consecutive Asia/Taipei business
dates, ending on the day being closed, on which the member has a CLOSED order.

Multiplier schedule (applied on top of the tier multiplier in orders_service):
- streak 1-2 days → 1.0x  (no change — keeps single-visit points exact)
- streak 3-4 days → 1.1x
- streak 5-6 days → 1.2x
- streak 7+  days → 1.5x

Pure reads; the caller owns the transaction.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Order, OrderStatus

# How far back to look — caps the query and is well beyond the 7-day top tier.
_STREAK_WINDOW_DAYS = 90

# (threshold_days, multiplier) — highest threshold first.
_STREAK_MULTIPLIERS: list[tuple[int, Decimal]] = [
    (7, Decimal("1.5")),
    (5, Decimal("1.2")),
    (3, Decimal("1.1")),
]


def streak_multiplier(streak: int) -> Decimal:
    """Points multiplier for a given consecutive-visit-day streak."""
    for threshold, mult in _STREAK_MULTIPLIERS:
        if streak >= threshold:
            return mult
    return Decimal("1.0")


async def compute_visit_streak(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    tenant_id: uuid.UUID,
    as_of: date,
) -> int:
    """Consecutive CLOSED-order business dates ending on ``as_of`` (0 if none)."""
    window_start = as_of - timedelta(days=_STREAK_WINDOW_DAYS)
    rows = (
        await session.execute(
            select(func.distinct(Order.business_date)).where(
                Order.customer_id == customer_id,
                Order.tenant_id == tenant_id,
                Order.status == OrderStatus.CLOSED,
                Order.deleted_at.is_(None),
                Order.business_date <= as_of,
                Order.business_date >= window_start,
            )
        )
    ).all()
    visit_days = {r[0] for r in rows}

    streak = 0
    day = as_of
    while day in visit_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


__all__ = ["compute_visit_streak", "streak_multiplier"]
