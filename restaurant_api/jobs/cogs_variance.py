"""Daily cost-control: detect when actual COGS deviates from theoretical by
> COGS_VARIANCE_THRESHOLD of yesterday's net revenue per (tenant, store).

Writes ``audit_log`` action=``cogs.variance.alert`` so the店長 sees an alert
on next dashboard load. Phase 2 also pushes to LINE via `LineMessenger.push`.

Threshold + window are pulled from settings so a future tenant_settings
table can override per-store without code changes.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_sessionmaker
from ..models import (
    DiscountKind,
    Order,
    OrderDiscount,
    OrderLine,
    OrderStatus,
)
from ..services.audit_service import audit
from ..services.calc.cogs_variance_detector import (
    VarianceInput,
    detect_variance,
)

logger = logging.getLogger("restaurant_api.jobs.cogs_variance")

# 5% of net revenue: the threshold from docs/04 mv_daily_pnl.
# Source of truth for the threshold lives in the calc engine; this constant
# is only used as the explicit override passed into VarianceInput so the
# audit-log payload reports it.
COGS_VARIANCE_THRESHOLD = Decimal("0.05")


async def run_cogs_variance_check(
    target_date: date | None = None,
    session: AsyncSession | None = None,
) -> int:
    """Compare actual vs theoretical COGS per (tenant, store) for one day.

    ``target_date`` defaults to yesterday. ``session`` lets tests inject one.
    """
    day = target_date or (date.today() - timedelta(days=1))
    if session is not None:
        return await _run(session, day)
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as own_session:
        n = await _run(own_session, day)
        await own_session.commit()
        return n


async def _run(session: AsyncSession, day: date) -> int:
    written = 0
    per_store_stmt = (
        select(
            Order.tenant_id,
            Order.store_id,
            func.sum(OrderLine.line_total).label("gross_revenue"),
            func.sum(OrderLine.cogs_actual).label("cogs_actual"),
            func.sum(OrderLine.cogs_theoretical).label("cogs_theoretical"),
        )
        .join(OrderLine, OrderLine.order_id == Order.id)
        .where(Order.business_date == day)
        .where(Order.status == OrderStatus.CLOSED)
        .group_by(Order.tenant_id, Order.store_id)
    )
    per_store = (await session.execute(per_store_stmt)).all()

    for row in per_store:
        gross = Decimal(row.gross_revenue or 0)
        actual = Decimal(row.cogs_actual or 0)
        theoretical = Decimal(row.cogs_theoretical or 0)

        disc_stmt = (
            select(func.coalesce(func.sum(OrderDiscount.value), 0))
            .join(Order, Order.id == OrderDiscount.order_id)
            .where(Order.tenant_id == row.tenant_id)
            .where(Order.store_id == row.store_id)
            .where(Order.business_date == day)
            .where(OrderDiscount.kind != DiscountKind.PERCENT)
        )
        discount_total = Decimal((await session.execute(disc_stmt)).scalar_one() or 0)
        net_revenue = gross - discount_total

        report = detect_variance(
            VarianceInput(
                business_date=day,
                store_id=str(row.store_id),
                actual_cogs=actual,
                theoretical_cogs=theoretical,
                net_revenue=net_revenue,
                threshold_pct=COGS_VARIANCE_THRESHOLD,
            )
        )
        if not report.flag:
            continue

        await audit(
            session,
            action="cogs.variance.alert",
            tenant_id=row.tenant_id,
            store_id=row.store_id,
            after={
                "business_date": day.isoformat(),
                "gross_revenue": str(gross),
                "net_revenue": str(net_revenue),
                "cogs_actual": str(actual),
                "cogs_theoretical": str(theoretical),
                "variance_abs": str(report.variance_abs),
                "variance_pct": str(report.variance_pct),
                "severity": report.severity,
                "threshold_pct": str(COGS_VARIANCE_THRESHOLD),
            },
            reason=f"COGS 變異 {report.variance_pct:.2%} 超過閾值 {COGS_VARIANCE_THRESHOLD:.0%}",
        )
        written += 1

    logger.info(
        "cogs_variance.complete",
        extra={"alerts_written": written, "target_date": day.isoformat()},
    )
    return written
