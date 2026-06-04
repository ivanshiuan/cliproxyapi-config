"""Daily food-safety: flag ingredients within EXPIRY_WARNING_DAYS of expiry that
still have positive on-hand quantity.

Writes one ``audit_log`` row per (tenant, store, ingredient) with action
``expiry.warning`` so the store dashboard can render a banner and (Phase 2)
push to the店長's LINE.

Idempotent within the same calendar day — re-running won't double-write.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_sessionmaker
from ..models import AuditLog, Ingredient, StockMovement
from ..services.audit_service import audit

logger = logging.getLogger("restaurant_api.jobs.expiry_warning")

EXPIRY_WARNING_DAYS = 3


async def run_expiry_warning(session: AsyncSession | None = None) -> int:
    """Returns the number of warnings written today.

    Optional ``session`` lets tests inject a savepoint-rolled session
    (so the writes are isolated). Production callers leave it None.
    """
    if session is not None:
        return await _scan_and_warn(session)
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as own_session:
        n = await _scan_and_warn(own_session)
        await own_session.commit()
        return n


async def _scan_and_warn(session: AsyncSession) -> int:
    today = date.today()
    horizon = today + timedelta(days=EXPIRY_WARNING_DAYS)
    written = 0

    already_warned_subq = (
        select(AuditLog.target_id)
        .where(AuditLog.action == "expiry.warning")
        .where(
            AuditLog.occurred_at
            >= datetime.combine(today, datetime.min.time(), tzinfo=UTC)
        )
    )

    on_hand_subq = (
        select(
            StockMovement.ingredient_id.label("ingredient_id"),
            func.sum(StockMovement.qty).label("qty"),
        )
        .group_by(StockMovement.ingredient_id)
        .subquery()
    )

    stmt = (
        select(Ingredient, on_hand_subq.c.qty)
        .join(on_hand_subq, on_hand_subq.c.ingredient_id == Ingredient.id, isouter=True)
        .where(Ingredient.expiry_date.is_not(None))
        .where(Ingredient.expiry_date <= horizon)
        .where(Ingredient.deleted_at.is_(None))
        .where(Ingredient.id.notin_(already_warned_subq))
    )

    rows = (await session.execute(stmt)).all()
    for ingredient, qty in rows:
        on_hand = Decimal(qty) if qty is not None else Decimal("0")
        if on_hand <= 0:
            continue
        days_left = (ingredient.expiry_date - today).days
        await audit(
            session,
            action="expiry.warning",
            tenant_id=ingredient.tenant_id,
            store_id=ingredient.store_id,
            target=("ingredients", ingredient.id),
            after={
                "lot_no": ingredient.lot_no,
                "expiry_date": ingredient.expiry_date.isoformat(),
                "days_left": days_left,
                "on_hand": str(on_hand),
                "name": ingredient.name,
            },
            reason=f"即期 {days_left}d, on_hand={on_hand}",
        )
        written += 1

    logger.info(
        "expiry_warning.complete",
        extra={"warnings_written": written, "horizon_days": EXPIRY_WARNING_DAYS},
    )
    return written
