"""Integration test for the nightly membership_lifecycle job.

Seeds one upgrade candidate, one birthday member, and one dormant member (all
LINE-reachable), runs the sweep, and asserts each got the right LINE push.
Assertions key on the specific ``line_user_id`` / message text rather than
estate-wide counts, since the job runs across all tenants.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.jobs.membership_lifecycle import run_membership_lifecycle
from restaurant_api.models import (
    Customer,
    CustomerTier,
    MenuItem,
    Order,
    OrderLine,
    OrderStatus,
    Store,
    Tenant,
)
from restaurant_api.services import membership_service

pytestmark = pytest.mark.asyncio


def _pushes_to(messenger: StubLineMessenger, line_id: str) -> list[str]:
    return [
        m["message"].text  # type: ignore[union-attr]
        for m in messenger.sent_messages
        if m.get("op") == "push" and m.get("to") == line_id
    ]


async def test_lifecycle_job_pushes_each_lever(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    seed_menu_item: MenuItem,
    stub_messenger: StubLineMessenger,
) -> None:
    now = datetime.now(UTC)
    today = membership_service._tpe_today()

    # Upgrade candidate — recent 5000 spend → GOLD.
    up = Customer(
        tenant_id=seed_tenant.id,
        display_name="升等客",
        line_user_id=f"U{uuid.uuid4().hex[:16]}",
    )
    db_session.add(up)
    await db_session.flush()
    order = Order(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        customer_id=up.id,
        order_no=f"T{uuid.uuid4().hex[:10]}",
        business_date=today,
        opened_at=now - timedelta(days=5),
        closed_at=now - timedelta(days=5),
        status=OrderStatus.CLOSED,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderLine(
            tenant_id=seed_tenant.id,
            order_id=order.id,
            menu_item_id=seed_menu_item.id,
            qty=Decimal("1"),
            unit_price=Decimal("5000"),
            line_total=Decimal("5000"),
        )
    )

    # Birthday member — birthday today.
    bday = Customer(
        tenant_id=seed_tenant.id,
        display_name="壽星",
        birthdate=date(1992, today.month, today.day),
        line_user_id=f"U{uuid.uuid4().hex[:16]}",
    )
    db_session.add(bday)

    # Dormant member — last visit 50 days ago.
    sleeper = Customer(
        tenant_id=seed_tenant.id,
        display_name="沉睡客",
        last_visit_at=now - timedelta(days=50),
        line_user_id=f"U{uuid.uuid4().hex[:16]}",
    )
    db_session.add(sleeper)
    await db_session.flush()

    summary = await run_membership_lifecycle(
        session=db_session, messenger=stub_messenger
    )

    assert set(summary) == {"tier_upgrades", "birthday_gifts", "winbacks"}

    up_msgs = _pushes_to(stub_messenger, up.line_user_id)
    assert any("升等" in m for m in up_msgs)
    assert up.tier == CustomerTier.GOLD

    bday_msgs = _pushes_to(stub_messenger, bday.line_user_id)
    assert any("生日快樂" in m for m in bday_msgs)

    sleeper_msgs = _pushes_to(stub_messenger, sleeper.line_user_id)
    assert any("好久不見" in m for m in sleeper_msgs)
