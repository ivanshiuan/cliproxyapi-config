"""Daily loyalty: write reversing rows for points whose ``expires_at`` has
passed without prior redemption.

Customer.points_balance is the cached running total — Phase 2 will refresh
it from the ledger after expiry rows are written. For Phase 1 we just write
the ledger truth; balance refresh ships with the customer router.

Idempotent within the same calendar day via the ``expire.batch`` reason key
+ original ledger row id lookup.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_sessionmaker
from ..models import CustomerPointsLedger
from ..services.audit_service import audit

logger = logging.getLogger("restaurant_api.jobs.points_expire")


async def run_points_expire(session: AsyncSession | None = None) -> int:
    """Returns the number of expiry-reversal rows written."""
    if session is not None:
        return await _scan_and_reverse(session)
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as own_session:
        n = await _scan_and_reverse(own_session)
        await own_session.commit()
        return n


async def _scan_and_reverse(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    written = 0

    unexpired_stmt = (
        select(CustomerPointsLedger)
        .where(CustomerPointsLedger.delta > 0)
        .where(CustomerPointsLedger.expires_at.is_not(None))
        .where(CustomerPointsLedger.expires_at <= now)
    )
    candidates = (await session.execute(unexpired_stmt)).scalars().all()

    already_reversed = {
        row.note
        for row in (
            await session.execute(
                select(CustomerPointsLedger).where(
                    CustomerPointsLedger.reason == "expire.batch"
                )
            )
        ).scalars()
        if row.note
    }

    for original in candidates:
        marker = f"expired_source:{original.id}"
        if marker in already_reversed:
            continue

        reversal = CustomerPointsLedger(
            tenant_id=original.tenant_id,
            customer_id=original.customer_id,
            delta=-original.delta,
            reason="expire.batch",
            source_order_id=original.source_order_id,
            note=marker,
        )
        session.add(reversal)
        await session.flush()

        await audit(
            session,
            action="points.expired",
            tenant_id=original.tenant_id,
            target=("customer_points_ledger", reversal.id),
            after={
                "customer_id": str(original.customer_id),
                "expired_points": str(original.delta),
                "original_id": str(original.id),
                "original_expires_at": original.expires_at.isoformat()
                if original.expires_at
                else None,
            },
            reason="nightly expiry batch",
        )
        written += 1

    logger.info(
        "points_expire.complete",
        extra={"expirations_written": written, "now": now.isoformat()},
    )
    return written
