"""Daily campaign maintenance: expire wheel-spin vouchers past their window.

A ``campaign_vouchers`` row is the mutable wallet record — this job flips any
``active`` voucher whose ``valid_until`` (an Asia/Taipei calendar date) is in
the past to ``expired``. ``redeem_voucher`` already does this lazily on access;
the nightly sweep keeps the wallet honest for everyone who *doesn't* try to
redeem, so reports and the member's wallet view reflect reality.

Idempotent: only ``active`` rows are scanned, so a second run the same day is a
no-op. ``redeemed`` / ``void`` vouchers are never touched.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_sessionmaker
from ..models import CampaignVoucher, VoucherStatus
from ..services.audit_service import audit

logger = logging.getLogger("restaurant_api.jobs.campaign_expiry")

_TPE = ZoneInfo("Asia/Taipei")


async def run_campaign_voucher_expiry(session: AsyncSession | None = None) -> int:
    """Expire stale active vouchers. Returns the number flipped to ``expired``."""
    if session is not None:
        return await _expire(session)
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as own_session:
        n = await _expire(own_session)
        await own_session.commit()
        return n


async def _expire(session: AsyncSession) -> int:
    today = datetime.now(_TPE).date()
    stmt = select(CampaignVoucher).where(
        CampaignVoucher.status == VoucherStatus.ACTIVE,
        CampaignVoucher.valid_until < today,
    )
    rows = (await session.execute(stmt)).scalars().all()

    expired = 0
    for voucher in rows:
        voucher.status = VoucherStatus.EXPIRED
        await session.flush()
        await audit(
            session,
            action="campaign.voucher_expired",
            tenant_id=voucher.tenant_id,
            target=("campaign_vouchers", voucher.id),
            before={"status": VoucherStatus.ACTIVE.value},
            after={
                "status": VoucherStatus.EXPIRED.value,
                "valid_until": voucher.valid_until.isoformat(),
            },
            reason="nightly campaign voucher expiry batch",
        )
        expired += 1

    logger.info(
        "campaign_voucher_expiry.complete",
        extra={"expired": expired, "today": today.isoformat()},
    )
    return expired
