"""Integration tests for the nightly campaign_expiry job.

End-to-end:
  1. A spin mints an active voucher with a validity window.
  2. We backdate ``valid_until`` so it's in the past.
  3. The job flips it active → expired and audits it.
  4. Re-run is a no-op (only ``active`` rows are scanned).
  5. In-date vouchers and already-redeemed vouchers are left alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from random import Random
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.jobs.campaign_expiry import run_campaign_voucher_expiry
from restaurant_api.models import (
    CampaignStatus,
    CampaignVoucher,
    MenuItem,
    Tenant,
    VoucherStatus,
)
from restaurant_api.schemas.campaigns import (
    CampaignCreate,
    PrizeCreate,
    SpinRequest,
    VoucherRedeemRequest,
)
from restaurant_api.services import campaigns_service

pytestmark = pytest.mark.asyncio

_TPE = ZoneInfo("Asia/Taipei")


def _today():
    return datetime.now(_TPE).date()


async def _mint_voucher(
    db_session: AsyncSession, tenant_id: uuid.UUID, menu_item_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create an active campaign + winning prize, spin once → return
    (campaign_id, voucher_id)."""
    campaign = await campaigns_service.create_campaign(
        db_session,
        CampaignCreate(
            name="開幕輪盤",
            slug=f"open-{uuid.uuid4().hex[:8]}",
            status=CampaignStatus.ACTIVE,
        ),
        tenant_id=tenant_id,
    )
    await campaigns_service.add_prize(
        db_session,
        campaign.id,
        PrizeCreate(name="和牛", weight=1, value_estimate=Decimal("500"), menu_item_id=menu_item_id),
        tenant_id=tenant_id,
    )
    spun = await campaigns_service.spin(
        db_session,
        campaign.id,
        SpinRequest(line_user_id=f"U{uuid.uuid4().hex[:16]}"),
        tenant_id=tenant_id,
        rng=Random(1),
    )
    assert spun.voucher is not None
    return campaign.id, spun.voucher.id


async def _backdate(db_session: AsyncSession, voucher_id: uuid.UUID) -> CampaignVoucher:
    row = (
        await db_session.execute(
            select(CampaignVoucher).where(CampaignVoucher.id == voucher_id)
        )
    ).scalar_one()
    row.valid_from = _today() - timedelta(days=30)
    row.valid_until = _today() - timedelta(days=1)
    await db_session.flush()
    return row


async def test_expired_voucher_flipped_to_expired(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    _, vid = await _mint_voucher(db_session, seed_tenant.id, seed_menu_item.id)
    row = await _backdate(db_session, vid)

    n = await run_campaign_voucher_expiry(session=db_session)
    assert n == 1

    await db_session.refresh(row)
    assert row.status == VoucherStatus.EXPIRED


async def test_in_date_voucher_untouched(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    _, vid = await _mint_voucher(db_session, seed_tenant.id, seed_menu_item.id)

    n = await run_campaign_voucher_expiry(session=db_session)
    assert n == 0

    row = (
        await db_session.execute(
            select(CampaignVoucher).where(CampaignVoucher.id == vid)
        )
    ).scalar_one()
    assert row.status == VoucherStatus.ACTIVE


async def test_job_is_idempotent(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    _, vid = await _mint_voucher(db_session, seed_tenant.id, seed_menu_item.id)
    await _backdate(db_session, vid)

    first = await run_campaign_voucher_expiry(session=db_session)
    second = await run_campaign_voucher_expiry(session=db_session)
    assert first == 1
    assert second == 0


async def test_redeemed_voucher_not_expired(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    """A redeemed voucher whose window later passes must stay redeemed —
    the sweep only touches ``active`` rows."""
    cid, vid = await _mint_voucher(db_session, seed_tenant.id, seed_menu_item.id)
    await campaigns_service.redeem_voucher(
        db_session, cid, vid, VoucherRedeemRequest(), tenant_id=seed_tenant.id
    )
    await _backdate(db_session, vid)

    n = await run_campaign_voucher_expiry(session=db_session)
    assert n == 0

    row = (
        await db_session.execute(
            select(CampaignVoucher).where(CampaignVoucher.id == vid)
        )
    ).scalar_one()
    assert row.status == VoucherStatus.REDEEMED
