"""儲值雙倍 — stored-value service tests (scoped to seed_tenant).

Covers: top-up bonus schedule, balance increment, spend deduction + overspend
guard, append-only ledger ordering, and the LINE confirmation push.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.errors import ConflictError, NotFoundError
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.models import Customer, CustomerStoredValueLedger, Tenant
from restaurant_api.schemas.stored_value import (
    StoredValueSpendRequest,
    StoredValueTopUpRequest,
)
from restaurant_api.services import stored_value_service

pytestmark = pytest.mark.asyncio


async def _customer(session: AsyncSession, tenant_id: uuid.UUID, **kw) -> Customer:
    cust = Customer(tenant_id=tenant_id, display_name="儲值客", **kw)
    session.add(cust)
    await session.flush()
    return cust


# ── bonus schedule ───────────────────────────────────────────────────────────


def test_bonus_schedule_brackets() -> None:
    assert stored_value_service.bonus_for(Decimal("300")) == Decimal("0")
    assert stored_value_service.bonus_for(Decimal("500")) == Decimal("50")  # 10%
    assert stored_value_service.bonus_for(Decimal("1000")) == Decimal("200")  # 20%
    assert stored_value_service.bonus_for(Decimal("3000")) == Decimal("750")  # 25%


# ── top-up ───────────────────────────────────────────────────────────────────


async def test_top_up_credits_amount_plus_bonus(
    db_session: AsyncSession, seed_tenant: Tenant, stub_messenger: StubLineMessenger
) -> None:
    cust = await _customer(
        db_session, seed_tenant.id, line_user_id=f"U{uuid.uuid4().hex[:16]}"
    )

    res = await stored_value_service.top_up(
        db_session,
        cust.id,
        StoredValueTopUpRequest(amount=Decimal("1000")),
        tenant_id=seed_tenant.id,
        messenger=stub_messenger,
    )

    # 1000 paid + 200 bonus = 1200
    assert res.stored_value_balance == Decimal("1200")
    assert cust.stored_value_balance == Decimal("1200")

    rows = await stored_value_service.list_ledger(
        db_session, cust.id, tenant_id=seed_tenant.id
    )
    reasons = {r.reason for r in rows}
    assert reasons == {"topup", "topup.bonus"}

    # LINE confirmation went out
    assert any(
        m.get("to") == cust.line_user_id and "儲值成功" in m["message"].text  # type: ignore[union-attr]
        for m in stub_messenger.sent_messages
    )


async def test_top_up_no_bonus_below_threshold(
    db_session: AsyncSession, seed_tenant: Tenant, stub_messenger: StubLineMessenger
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    res = await stored_value_service.top_up(
        db_session,
        cust.id,
        StoredValueTopUpRequest(amount=Decimal("300")),
        tenant_id=seed_tenant.id,
        messenger=stub_messenger,
    )
    assert res.stored_value_balance == Decimal("300")
    rows = await stored_value_service.list_ledger(
        db_session, cust.id, tenant_id=seed_tenant.id
    )
    assert [r.reason for r in rows] == ["topup"]


# ── spend ────────────────────────────────────────────────────────────────────


async def test_spend_deducts_balance(
    db_session: AsyncSession, seed_tenant: Tenant, stub_messenger: StubLineMessenger
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    await stored_value_service.top_up(
        db_session,
        cust.id,
        StoredValueTopUpRequest(amount=Decimal("1000")),
        tenant_id=seed_tenant.id,
        messenger=stub_messenger,
    )  # balance 1200

    res = await stored_value_service.spend(
        db_session,
        cust.id,
        StoredValueSpendRequest(amount=Decimal("450")),
        tenant_id=seed_tenant.id,
    )
    assert res.stored_value_balance == Decimal("750")


async def test_spend_rejects_overspend(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    with pytest.raises(ConflictError):
        await stored_value_service.spend(
            db_session,
            cust.id,
            StoredValueSpendRequest(amount=Decimal("100")),
            tenant_id=seed_tenant.id,
        )


async def test_top_up_unknown_customer_404(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        await stored_value_service.top_up(
            db_session,
            uuid.uuid4(),
            StoredValueTopUpRequest(amount=Decimal("500")),
            tenant_id=seed_tenant.id,
        )


# ── append-only guarantee ────────────────────────────────────────────────────


async def test_ledger_is_append_only(
    db_session: AsyncSession, seed_tenant: Tenant, stub_messenger: StubLineMessenger
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    await stored_value_service.top_up(
        db_session,
        cust.id,
        StoredValueTopUpRequest(amount=Decimal("500")),
        tenant_id=seed_tenant.id,
        messenger=stub_messenger,
    )
    # The DB RULE turns UPDATE/DELETE into a no-op (row count 0 affected).
    await db_session.execute(
        text("UPDATE customer_stored_value_ledger SET delta = 99999 WHERE customer_id = :c"),
        {"c": str(cust.id)},
    )
    rows = (
        await db_session.execute(
            CustomerStoredValueLedger.__table__.select().where(
                CustomerStoredValueLedger.customer_id == cust.id
            )
        )
    ).all()
    assert all(r.delta != Decimal("99999") for r in rows)
