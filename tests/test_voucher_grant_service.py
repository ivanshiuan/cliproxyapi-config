"""貴人配額券 — 模式 B voucher_grant_service tests (scoped to seed_tenant).

Focus: the hard quota ceiling and expiry that keep VIP distribution from running
away (docs/18 §3.3 / §4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.errors import ConflictError, NotFoundError, ValidationError
from restaurant_api.models import Tenant
from restaurant_api.services import voucher_grant_service as svc

pytestmark = pytest.mark.asyncio


def _today():
    return datetime.now(UTC).date()


# ── create ───────────────────────────────────────────────────────────────────


async def test_create_grant(db_session: AsyncSession, seed_tenant: Tenant) -> None:
    grant = await svc.create_grant(
        db_session,
        tenant_id=seed_tenant.id,
        grantee_name="股東A",
        voucher_kind="cash_credit",
        total_quota=20,
        valid_until=_today() + timedelta(days=90),
        face_value=Decimal("200"),
    )
    assert grant.used_count == 0
    assert grant.total_quota == 20
    assert grant.face_value == Decimal("200")


async def test_create_grant_rejects_nonpositive_quota(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    with pytest.raises(ValidationError):
        await svc.create_grant(
            db_session,
            tenant_id=seed_tenant.id,
            grantee_name="x",
            voucher_kind="cash_credit",
            total_quota=0,
            valid_until=_today() + timedelta(days=1),
        )


# ── distribute + ceiling ──────────────────────────────────────────────────────


async def test_distribute_increments_used_count(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    grant = await svc.create_grant(
        db_session,
        tenant_id=seed_tenant.id,
        grantee_name="股東B",
        voucher_kind="hotpot_base",
        total_quota=3,
        valid_until=_today() + timedelta(days=30),
    )
    for expected in (1, 2, 3):
        updated = await svc.distribute(
            db_session, grant_id=grant.id, tenant_id=seed_tenant.id
        )
        assert updated.used_count == expected


async def test_distribute_past_quota_conflicts(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """Hard ceiling: the (quota+1)-th draw is rejected."""
    grant = await svc.create_grant(
        db_session,
        tenant_id=seed_tenant.id,
        grantee_name="股東C",
        voucher_kind="meat_platter",
        total_quota=2,
        valid_until=_today() + timedelta(days=30),
    )
    await svc.distribute(db_session, grant_id=grant.id, tenant_id=seed_tenant.id)
    await svc.distribute(db_session, grant_id=grant.id, tenant_id=seed_tenant.id)
    with pytest.raises(ConflictError):
        await svc.distribute(db_session, grant_id=grant.id, tenant_id=seed_tenant.id)


async def test_distribute_after_expiry_conflicts(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    grant = await svc.create_grant(
        db_session,
        tenant_id=seed_tenant.id,
        grantee_name="股東D",
        voucher_kind="cash_credit",
        total_quota=5,
        valid_until=_today() - timedelta(days=1),  # already expired
    )
    with pytest.raises(ConflictError):
        await svc.distribute(db_session, grant_id=grant.id, tenant_id=seed_tenant.id)


async def test_distribute_unknown_grant_404(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        await svc.distribute(
            db_session, grant_id=uuid.uuid4(), tenant_id=seed_tenant.id
        )


async def test_list_grants_newest_first(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    a = await svc.create_grant(
        db_session,
        tenant_id=seed_tenant.id,
        grantee_name="第一位",
        voucher_kind="cash_credit",
        total_quota=1,
        valid_until=_today() + timedelta(days=10),
    )
    b = await svc.create_grant(
        db_session,
        tenant_id=seed_tenant.id,
        grantee_name="第二位",
        voucher_kind="cash_credit",
        total_quota=1,
        valid_until=_today() + timedelta(days=10),
    )
    rows = await svc.list_grants(db_session, tenant_id=seed_tenant.id)
    ids = [g.id for g in rows]
    # both present; b (created later) comes before a
    assert ids.index(b.id) < ids.index(a.id)
