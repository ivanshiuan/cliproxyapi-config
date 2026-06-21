"""Reset the 開幕輪盤 campaign to a clean pre-launch state.

Why this exists: load-testing (``scripts/wheel_uxtest.py``) writes real spins,
vouchers and members, and bumps ``awarded_count`` — which burns the capped
grand-prize quota. Run this once before going live to:

  1. delete the test spins + vouchers for the campaign (clean dashboard),
  2. reset every prize's ``awarded_count`` to 0 (un-burn the quotas),
  3. apply the production quota caps (和牛一盤 was uncapped — give it a ceiling).

It does NOT touch the append-only ledgers (audit_log, points_ledger), and it
keeps the campaign id stable so the printed QR / rich-menu / Flex links stay
valid. Test member rows are harmless and left in place.

Usage:
    .venv/bin/python scripts/wheel_reset_for_launch.py            # grand-open
    .venv/bin/python scripts/wheel_reset_for_launch.py <slug>
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

from restaurant_api.config import get_settings
from restaurant_api.database import dispose_engine, get_sessionmaker
from restaurant_api.models import (
    CampaignPrize,
    CampaignSpin,
    CampaignVoucher,
    MarketingCampaign,
)

# Production quota ceilings (None = uncapped). Keyed by prize name.
_CAPS: dict[str, int | None] = {
    "免單四人套餐": 3,
    "雙人套餐": 10,
    "和牛一盤": 40,      # ← was uncapped; this is the fix
    "招待飲料一杯": None,  # cheap, deliberately abundant
    "銘謝惠顧": None,
}


async def _reset(slug: str) -> None:
    settings = get_settings()
    tenant_id = settings.default_tenant_id
    async with get_sessionmaker()() as session:
        campaign = (
            await session.execute(
                select(MarketingCampaign).where(
                    MarketingCampaign.tenant_id == tenant_id,
                    MarketingCampaign.slug == slug,
                    MarketingCampaign.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if campaign is None:
            print(f"❌ 找不到 slug={slug} 的活動（先跑 make wheel-demo）")
            return

        cid = campaign.id
        # 1. purge test vouchers + spins (not append-only; safe to delete).
        # ``execute(delete(...))`` returns a CursorResult whose ``rowcount`` the
        # base ``Result`` stub doesn't expose — cast for a clean type-check.
        v_res = cast("CursorResult[Any]", await session.execute(
            delete(CampaignVoucher).where(CampaignVoucher.campaign_id == cid)
        ))
        v_del = v_res.rowcount
        s_res = cast("CursorResult[Any]", await session.execute(
            delete(CampaignSpin).where(CampaignSpin.campaign_id == cid)
        ))
        s_del = s_res.rowcount

        # 2 + 3. reset counts and apply caps
        prizes = (
            (
                await session.execute(
                    select(CampaignPrize)
                    .where(CampaignPrize.campaign_id == cid)
                    .order_by(CampaignPrize.sort_order)
                )
            )
            .scalars()
            .all()
        )
        for p in prizes:
            p.awarded_count = 0
            if p.name in _CAPS:
                p.total_quota = _CAPS[p.name]

        await session.commit()

        print(f"✅ 活動 {cid} (slug={slug}) 已重置為上線前乾淨狀態")
        print(f"   清掉測試券 {v_del} 張、測試抽獎 {s_del} 筆")
        print("   獎項配額（已套用上線上限）:")
        for p in prizes:
            cap = "∞" if p.total_quota is None else p.total_quota
            print(f"     {p.name:<14} 上限={cap}  已發=0  價值={p.value_estimate}")
    await dispose_engine()


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "grand-open"
    asyncio.run(_reset(slug))


if __name__ == "__main__":
    main()
