"""UGC (打卡 / 評論換獎) service tests (scoped to seed_tenant).

Covers: submit → pending queue, approve grants kind-based points + LINE,
reject grants nothing, and double-review is rejected (one-shot).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.errors import ConflictError, NotFoundError
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.models import Customer, CustomerPointsLedger, UgcKind, UgcStatus
from restaurant_api.schemas.ugc import UgcReviewRequest, UgcSubmitRequest
from restaurant_api.services import ugc_service

pytestmark = pytest.mark.asyncio


async def _customer(session: AsyncSession, tenant_id: uuid.UUID, **kw) -> Customer:
    cust = Customer(tenant_id=tenant_id, display_name="貼文客", **kw)
    session.add(cust)
    await session.flush()
    return cust


async def _ugc_points(session: AsyncSession, customer_id: uuid.UUID) -> Decimal:
    return Decimal(
        (
            await session.execute(
                select(func.coalesce(func.sum(CustomerPointsLedger.delta), 0)).where(
                    CustomerPointsLedger.customer_id == customer_id,
                    CustomerPointsLedger.reason == "ugc.reward",
                )
            )
        ).scalar_one()
    )


async def test_submit_enters_pending_queue(
    db_session: AsyncSession, seed_tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    res = await ugc_service.submit(
        db_session,
        UgcSubmitRequest(customer_id=cust.id, kind=UgcKind.GOOGLE_REVIEW),
        tenant_id=seed_tenant.id,
    )
    assert res.status == UgcStatus.PENDING

    pending = await ugc_service.list_submissions(
        db_session, tenant_id=seed_tenant.id, status=UgcStatus.PENDING
    )
    assert any(s.id == res.id for s in pending)


async def test_submit_unknown_customer_404(
    db_session: AsyncSession, seed_tenant
) -> None:
    with pytest.raises(NotFoundError):
        await ugc_service.submit(
            db_session,
            UgcSubmitRequest(customer_id=uuid.uuid4(), kind=UgcKind.CHECKIN),
            tenant_id=seed_tenant.id,
        )


async def test_approve_grants_kind_based_points_and_pushes(
    db_session: AsyncSession, seed_tenant, stub_messenger: StubLineMessenger
) -> None:
    cust = await _customer(
        db_session, seed_tenant.id, line_user_id=f"U{uuid.uuid4().hex[:16]}"
    )
    sub = await ugc_service.submit(
        db_session,
        UgcSubmitRequest(customer_id=cust.id, kind=UgcKind.GOOGLE_REVIEW),
        tenant_id=seed_tenant.id,
    )

    res = await ugc_service.approve(
        db_session,
        sub.id,
        UgcReviewRequest(),
        tenant_id=seed_tenant.id,
        messenger=stub_messenger,
    )
    assert res.status == UgcStatus.APPROVED
    assert res.reward_points == Decimal("100")  # google_review
    assert await _ugc_points(db_session, cust.id) == Decimal("100")
    assert any(
        m.get("to") == cust.line_user_id  # type: ignore[union-attr]
        for m in stub_messenger.sent_messages
    )


async def test_double_review_conflicts(
    db_session: AsyncSession, seed_tenant, stub_messenger: StubLineMessenger
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    sub = await ugc_service.submit(
        db_session,
        UgcSubmitRequest(customer_id=cust.id, kind=UgcKind.IG_POST),
        tenant_id=seed_tenant.id,
    )
    await ugc_service.approve(
        db_session, sub.id, UgcReviewRequest(), tenant_id=seed_tenant.id,
        messenger=stub_messenger,
    )
    with pytest.raises(ConflictError):
        await ugc_service.approve(
            db_session, sub.id, UgcReviewRequest(), tenant_id=seed_tenant.id,
            messenger=stub_messenger,
        )


async def test_reject_grants_nothing(
    db_session: AsyncSession, seed_tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    sub = await ugc_service.submit(
        db_session,
        UgcSubmitRequest(customer_id=cust.id, kind=UgcKind.CHECKIN),
        tenant_id=seed_tenant.id,
    )
    res = await ugc_service.reject(
        db_session, sub.id, UgcReviewRequest(note="模糊截圖"), tenant_id=seed_tenant.id
    )
    assert res.status == UgcStatus.REJECTED
    assert await _ugc_points(db_session, cust.id) == Decimal("0")
