"""Seed a full 成長飛輪 demo so /membership/stats and /membership/segments show
real numbers end-to-end.

Run after ``make db-migrate``:

    python scripts/seed_growth_demo.py

Everything lands under ``settings.default_tenant_id`` (the tenant the API falls
back to when multi-tenancy is off), so the dashboards resolve without a tenant
header. Exercises every growth system:

- 儲值 (stored value): a top-up + bonus
- 裂變 (referral): an attributed + qualified referral edge
- UGC: one approved Google review + one pending submission
- 連續回訪 / RFM: members seeded across every RFM segment via order history

Idempotent: keyed on a sentinel customer phone; re-running just recomputes and
reprints the dashboard summary.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from restaurant_api.config import get_settings
from restaurant_api.database import dispose_engine, get_sessionmaker
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.models import (
    Customer,
    CustomerTier,
    MenuItem,
    Order,
    OrderStatus,
    Store,
    Tenant,
    UgcKind,
)
from restaurant_api.schemas.stored_value import StoredValueTopUpRequest
from restaurant_api.schemas.ugc import UgcReviewRequest, UgcSubmitRequest
from restaurant_api.services import (
    membership_stats_service,
    referral_service,
    rfm_service,
    stored_value_service,
    ugc_service,
)

_SENTINEL_PHONE = "0900000001"  # champion member — presence means "already seeded"


async def _member(
    session,
    tenant_id: uuid.UUID,
    *,
    name: str,
    tier: CustomerTier = CustomerTier.REGULAR,
    lifetime_value: Decimal = Decimal("0"),
    phone: str | None = None,
    line: bool = True,
) -> Customer:
    cust = Customer(
        tenant_id=tenant_id,
        display_name=name,
        tier=tier,
        lifetime_value=lifetime_value,
        phone=phone,
        line_user_id=f"Udemo{uuid.uuid4().hex[:14]}" if line else None,
    )
    session.add(cust)
    await session.flush()
    return cust


async def _closed_orders(
    session,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    customer_id: uuid.UUID,
    business_dates: list[date],
) -> Order | None:
    last: Order | None = None
    for d in business_dates:
        last = Order(
            tenant_id=tenant_id,
            store_id=store_id,
            customer_id=customer_id,
            order_no=f"DEMO-{uuid.uuid4().hex[:10]}",
            business_date=d,
            opened_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
            status=OrderStatus.CLOSED,
        )
        session.add(last)
    await session.flush()
    return last


async def _seed() -> None:
    settings = get_settings()
    tenant_id = uuid.UUID(settings.default_tenant_id)
    messenger = StubLineMessenger()
    today = date.today()
    SessionLocal = get_sessionmaker()

    async with SessionLocal() as session:
        # 1) Tenant + store + menu item (shared with the wheel demo seed).
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            tenant = Tenant(id=tenant_id, name="成長示範餐廳", slug="growth-demo-tenant")
            session.add(tenant)
            await session.flush()

        store = (
            await session.execute(
                select(Store).where(Store.tenant_id == tenant_id).limit(1)
            )
        ).scalar_one_or_none()
        if store is None:
            store = Store(
                tenant_id=tenant_id,
                name="示範門市",
                address="台北市信義區",
                phone="02-1234-5678",
                opened_on=today,
                is_active=True,
            )
            session.add(store)
            await session.flush()

        item = (
            await session.execute(
                select(MenuItem).where(MenuItem.tenant_id == tenant_id).limit(1)
            )
        ).scalar_one_or_none()
        if item is None:
            item = MenuItem(
                tenant_id=tenant_id,
                store_id=store.id,
                sku="DEMO-MENU-01",
                name="招牌餐",
                price=Decimal("300.00"),
                cost_estimate=Decimal("90.00"),
                is_available=True,
                allergens=[],
            )
            session.add(item)
            await session.flush()

        # 2) Idempotency sentinel.
        already = (
            await session.execute(
                select(Customer).where(
                    Customer.tenant_id == tenant_id,
                    Customer.phone == _SENTINEL_PHONE,
                )
            )
        ).scalar_one_or_none()

        if already is None:
            # CHAMPION: 12 consecutive recent days, high spend, on LINE.
            champ = await _member(
                session, tenant_id, name="冠軍客",
                tier=CustomerTier.PLATINUM, lifetime_value=Decimal("20000"),
                phone=_SENTINEL_PHONE,
            )
            await _closed_orders(
                session, tenant_id=tenant_id, store_id=store.id,
                customer_id=champ.id,
                business_dates=[today - timedelta(days=k) for k in range(12)],
            )
            # 儲值: champion tops up 3000 (+750 bonus).
            await stored_value_service.top_up(
                session, champ.id,
                StoredValueTopUpRequest(amount=Decimal("3000")),
                tenant_id=tenant_id, messenger=messenger,
            )

            # LOYAL: 5 recent visits, mid spend.
            loyal = await _member(
                session, tenant_id, name="忠誠客",
                tier=CustomerTier.GOLD, lifetime_value=Decimal("4000"),
            )
            await _closed_orders(
                session, tenant_id=tenant_id, store_id=store.id,
                customer_id=loyal.id,
                business_dates=[today - timedelta(days=k * 3) for k in range(5)],
            )

            # AT_RISK: was frequent, gone quiet 60 days.
            risk = await _member(
                session, tenant_id, name="流失邊緣客",
                tier=CustomerTier.SILVER, lifetime_value=Decimal("8000"),
            )
            await _closed_orders(
                session, tenant_id=tenant_id, store_id=store.id,
                customer_id=risk.id,
                business_dates=[today - timedelta(days=60 + k) for k in range(5)],
            )

            # DORMANT: a single visit long ago, low value.
            dormant = await _member(
                session, tenant_id, name="沉睡客", lifetime_value=Decimal("300"),
            )
            await _closed_orders(
                session, tenant_id=tenant_id, store_id=store.id,
                customer_id=dormant.id,
                business_dates=[today - timedelta(days=120)],
            )

            # 裂變: referrer shares a code, friend signs up + qualifies on a visit.
            referrer = await _member(
                session, tenant_id, name="推薦人", lifetime_value=Decimal("1500"),
            )
            code = await referral_service.ensure_referral_code(
                session, referrer, tenant_id=tenant_id
            )
            friend = await _member(session, tenant_id, name="被推薦的新朋友")
            await referral_service.attribute_referral(
                session, referred=friend, code=code, tenant_id=tenant_id
            )
            friend_order = await _closed_orders(
                session, tenant_id=tenant_id, store_id=store.id,
                customer_id=friend.id, business_dates=[today],
            )
            assert friend_order is not None
            await referral_service.qualify_referral(
                session, referred_id=friend.id, tenant_id=tenant_id,
                order_id=friend_order.id, messenger=messenger,
            )

            # UGC: one approved Google review + one pending submission.
            sub = await ugc_service.submit(
                session,
                UgcSubmitRequest(customer_id=champ.id, kind=UgcKind.GOOGLE_REVIEW),
                tenant_id=tenant_id,
            )
            await ugc_service.approve(
                session, sub.id, UgcReviewRequest(), tenant_id=tenant_id,
                messenger=messenger,
            )
            await ugc_service.submit(
                session,
                UgcSubmitRequest(customer_id=loyal.id, kind=UgcKind.IG_POST),
                tenant_id=tenant_id,
            )

            # NEW: registered, no activity yet.
            await _member(session, tenant_id, name="剛加入的會員")

            await session.commit()
            print("✅ 成長飛輪示範資料已建立")
        else:
            print("ℹ️  示範資料已存在，略過建立，僅重新計算")

        # 3) Recompute + print the dashboards.
        stats = await membership_stats_service.compute_stats(
            session, tenant_id=tenant_id
        )
        segs = await rfm_service.segment_counts(session, tenant_id=tenant_id)

    print("\n── /membership/stats ──")
    print(f"  會員總數      : {stats.total_members}")
    print(f"  點數未兌換負債: {stats.points.outstanding_balance}")
    print(f"  儲值未用餘額  : {stats.stored_value.outstanding_balance} "
          f"(滲透率 {stats.stored_value.penetration_pct}%)")
    print(f"  裂變          : {stats.referral.qualified}/{stats.referral.total} 達標 "
          f"(轉換率 {stats.referral.conversion_pct}%, K={stats.referral.k_factor})")
    print(f"  UGC           : approved={stats.ugc.approved} pending={stats.ugc.pending} "
          f"(通過率 {stats.ugc.approval_pct}%)")
    print("\n── /membership/segments (RFM) ──")
    for bucket in segs.segments:
        print(f"  {bucket.segment.value:<10}: {bucket.count}")
    print("\n啟動 API 後可打:  GET /membership/stats   GET /membership/segments")


def main() -> None:
    try:
        asyncio.run(_seed())
    finally:
        asyncio.run(dispose_engine())


if __name__ == "__main__":
    main()
