"""貴人配額券 — 模式 B VIP / shareholder quota-grant service.

The operator hands a shareholder / 貴人 a **capped** allotment of vouchers
("$200 抵用券 x20 + 鍋底券 x10 …"). They distribute to friends who come eat.
Unlike 模式 A this grants the distributor **no reward** — they're spending the
operator's allotment to do a favour, not earning — and ``total_quota`` is a hard
ceiling so distribution can never run away (see ``docs/18_voucher_sharing_design.md``).

Correctness:
- ``distribute`` locks the grant row ``FOR UPDATE`` before the read-modify-write on
  ``used_count``, so two friends drawing from the same allotment concurrently can't
  both slip past a nearly-exhausted quota (the ceiling is enforced under the lock).
- Distributing past ``total_quota`` or after ``valid_until`` raises ``ConflictError``.
- ``flush()`` only; the caller owns the transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models import VoucherGrant
from .audit_service import audit


async def create_grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    grantee_name: str,
    voucher_kind: str,
    total_quota: int,
    valid_until: date,
    face_value: Decimal = Decimal("0"),
    menu_item_id: uuid.UUID | None = None,
    grantee_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> VoucherGrant:
    """Create a capped voucher allotment for a shareholder / 貴人."""
    if total_quota <= 0:
        raise ValidationError(
            message="total_quota must be positive",
            details={"total_quota": total_quota},
        )
    grant = VoucherGrant(
        tenant_id=tenant_id,
        grantee_name=grantee_name,
        grantee_id=grantee_id,
        voucher_kind=voucher_kind,
        face_value=face_value,
        menu_item_id=menu_item_id,
        total_quota=total_quota,
        used_count=0,
        valid_until=valid_until,
    )
    session.add(grant)
    await session.flush()
    await audit(
        session,
        action="voucher_grant.created",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("voucher_grants", grant.id),
        after={
            "grantee_name": grantee_name,
            "voucher_kind": voucher_kind,
            "total_quota": total_quota,
            "face_value": str(face_value),
            "valid_until": valid_until.isoformat(),
        },
    )
    return grant


async def distribute(
    session: AsyncSession,
    *,
    grant_id: uuid.UUID,
    tenant_id: uuid.UUID,
    as_of: date | None = None,
) -> VoucherGrant:
    """Draw one voucher from the allotment (increments ``used_count``).

    Enforces the quota ceiling and expiry under a row lock. Returns the updated
    grant so the caller can read the remaining headroom.
    """
    today = as_of or datetime.now(UTC).date()
    grant = (
        await session.execute(
            select(VoucherGrant)
            .where(VoucherGrant.id == grant_id, VoucherGrant.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if grant is None:
        raise NotFoundError(
            message=f"grant {grant_id} not found",
            details={"grant_id": str(grant_id)},
        )
    if today > grant.valid_until:
        raise ConflictError(
            message="this allotment has expired",
            details={"valid_until": grant.valid_until.isoformat()},
        )
    if grant.used_count >= grant.total_quota:
        raise ConflictError(
            message="allotment quota exhausted",
            details={"total_quota": grant.total_quota, "used_count": grant.used_count},
        )

    grant.used_count += 1
    await session.flush()
    await audit(
        session,
        action="voucher_grant.distributed",
        tenant_id=tenant_id,
        target=("voucher_grants", grant.id),
        after={"used_count": grant.used_count, "total_quota": grant.total_quota},
    )
    return grant


async def list_grants(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[VoucherGrant]:
    """All allotments for the tenant, newest first — the operator's dashboard."""
    rows = (
        await session.execute(
            select(VoucherGrant)
            .where(VoucherGrant.tenant_id == tenant_id)
            .order_by(VoucherGrant.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


__all__ = [
    "create_grant",
    "distribute",
    "list_grants",
]
