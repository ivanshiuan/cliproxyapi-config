"""Seed an ACTIVE wheel-spin campaign so the demo page works end-to-end.

Run after ``make db-migrate``:

    python scripts/seed_wheel_campaign.py

Creates everything under ``settings.default_tenant_id`` (the all-zeros tenant
the API falls back to when multi-tenancy is off) so the demo page — which sends
no tenant header — resolves to the same tenant. Then prints the demo URL:

    http://localhost:8000/demo/?campaign=<id>

Idempotent: re-running reuses the existing campaign (matched by slug) and just
reprints the URL.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from restaurant_api.config import get_settings
from restaurant_api.database import dispose_engine, get_sessionmaker
from restaurant_api.models import (
    CampaignPrize,
    CampaignStatus,
    MarketingCampaign,
    MenuItem,
    Store,
    Tenant,
)

_CAMPAIGN_SLUG = "grand-open"

# (name, weight, value, has_menu_item, total_quota) — grand prize is rare AND capped.
_PRIZES = [
    ("免單四人套餐", 1, "3200", True, 3),
    ("雙人套餐", 2, "1280", True, 10),
    ("和牛一盤", 8, "600", True, 40),
    ("招待飲料一杯", 30, "60", True, None),
    ("銘謝惠顧", 40, "0", False, None),
]


async def _seed() -> str:
    settings = get_settings()
    tenant_id = uuid.UUID(settings.default_tenant_id)
    SessionLocal = get_sessionmaker()

    async with SessionLocal() as session:
        # 1) Default tenant.
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            tenant = Tenant(id=tenant_id, name="輪盤示範餐廳", slug="wheel-demo-tenant")
            session.add(tenant)
            await session.flush()

        # 2) A store.
        store = (
            await session.execute(
                select(Store).where(Store.tenant_id == tenant_id).limit(1)
            )
        ).scalar_one_or_none()
        if store is None:
            store = Store(
                tenant_id=tenant_id,
                name="開幕門市",
                address="台北市信義區",
                phone="02-1234-5678",
                opened_on=date.today(),
                is_active=True,
            )
            session.add(store)
            await session.flush()

        # 3) A menu item to back the food prizes.
        item = (
            await session.execute(
                select(MenuItem).where(MenuItem.tenant_id == tenant_id).limit(1)
            )
        ).scalar_one_or_none()
        if item is None:
            item = MenuItem(
                tenant_id=tenant_id,
                store_id=store.id,
                sku="DEMO-PRIZE-01",
                name="主廚招待餐",
                price=Decimal("600.00"),
                cost_estimate=Decimal("180.00"),
                is_available=True,
                allergens=[],
            )
            session.add(item)
            await session.flush()

        # 4) Campaign (idempotent on slug).
        campaign = (
            await session.execute(
                select(MarketingCampaign).where(
                    MarketingCampaign.tenant_id == tenant_id,
                    MarketingCampaign.slug == _CAMPAIGN_SLUG,
                    MarketingCampaign.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if campaign is None:
            campaign = MarketingCampaign(
                tenant_id=tenant_id,
                store_id=store.id,
                name="開幕輪盤抽獎",
                slug=_CAMPAIGN_SLUG,
                status=CampaignStatus.ACTIVE,
                daily_spin_limit=1,
                voucher_start_offset_days=0,
                voucher_validity_days=14,
                daily_message="今天也來抽一波！本週主廚菜 8 折，憑券來店就送。",
            )
            session.add(campaign)
            await session.flush()
            for name, weight, value, has_item, quota in _PRIZES:
                session.add(
                    CampaignPrize(
                        tenant_id=tenant_id,
                        campaign_id=campaign.id,
                        name=name,
                        weight=weight,
                        value_estimate=Decimal(value),
                        menu_item_id=item.id if has_item else None,
                        total_quota=quota,
                        is_active=True,
                    )
                )
            await session.flush()
            created = True
        else:
            created = False

        await session.commit()
        verb = "建立" if created else "已存在"
        print(f"✅ 活動{verb}：{campaign.name} (slug={campaign.slug})")
        print(f"   campaign_id = {campaign.id}")
        return str(campaign.id)


async def _main() -> None:
    cid = await _seed()
    await dispose_engine()
    print()
    print("👉 啟動 API：  make api")
    print(f"👉 開啟 demo： http://localhost:8000/demo/?campaign={cid}")
    print("   （手機掃 QR Code 時，把 localhost 換成你的對外網址）")


if __name__ == "__main__":
    asyncio.run(_main())
