"""自動折扣規則 (docs/22 #10) — CRUD + pure matching functions.

CRUD is flush-only (the ``get_db`` DI owns the transaction). The matching
half (``best_rule``/``rule_discount``) is pure and side-effect-free so
``pos_order_service`` can call it from every money path (quote/checkout/
拆單/外帶) and tests can pin the semantics without a DB.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import NotFoundError
from ..models import DiscountKind, DiscountRule
from ..schemas.discount_rules import (
    DiscountRuleCreate,
    DiscountRuleResponse,
    DiscountRuleUpdate,
)
from .audit_service import audit

_TPE = ZoneInfo("Asia/Taipei")
_MONEY_Q = Decimal("0.01")


# ──────────────────────────────────────────────────────────────────────────
# Pure matching (no DB, no clock — caller passes gross + now)
# ──────────────────────────────────────────────────────────────────────────


def _in_window(rule: DiscountRule, now: time) -> bool:
    start, end = rule.start_time, rule.end_time
    if start is None or end is None:
        return True
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # wraps past midnight (e.g. 21:00-02:00)


def rule_matches(rule: DiscountRule, gross: Decimal, now: time) -> bool:
    if rule.min_subtotal is not None and gross < rule.min_subtotal:
        return False
    return _in_window(rule, now)


def rule_discount(rule: DiscountRule, gross: Decimal) -> Decimal:
    """TWD the rule takes off a ``gross`` bill (used only to RANK rules —
    the actual money math stays in the ``amount_due`` discount stack)."""
    if rule.kind == DiscountKind.PERCENT:
        return (gross * rule.value).quantize(_MONEY_Q)
    return min(rule.value, gross)


def best_rule(rules: list[DiscountRule], gross: Decimal, now: time) -> DiscountRule | None:
    """The single matching rule that discounts the most (auto rules don't
    stack — one promo per bill, manual discounts stack on top as before).
    Ties break to the oldest rule for determinism."""
    candidates = [r for r in rules if rule_matches(r, gross, now)]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (rule_discount(r, gross), -r.created_at.timestamp()))


async def active_rules_for_store(
    session: AsyncSession, *, tenant_id: uuid.UUID, store_id: uuid.UUID
) -> list[DiscountRule]:
    """Active rules that apply to this store (store-specific + tenant-wide)."""
    return list(
        (
            await session.execute(
                select(DiscountRule).where(
                    DiscountRule.tenant_id == tenant_id,
                    or_(DiscountRule.store_id == store_id, DiscountRule.store_id.is_(None)),
                    DiscountRule.is_active.is_(True),
                    DiscountRule.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )


def taipei_now() -> time:
    return datetime.now(_TPE).time()


# ──────────────────────────────────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────────────────────────────────


async def create_rule(
    session: AsyncSession,
    payload: DiscountRuleCreate,
    *,
    tenant_id: uuid.UUID,
) -> DiscountRuleResponse:
    row = DiscountRule(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        name=payload.name,
        kind=DiscountKind(payload.kind),
        value=payload.value,
        min_subtotal=payload.min_subtotal,
        start_time=payload.start_time,
        end_time=payload.end_time,
        is_active=payload.is_active,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="discount_rule.created",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        target=("discount_rules", row.id),
        after={
            "name": payload.name,
            "kind": payload.kind,
            "value": str(payload.value),
            "min_subtotal": str(payload.min_subtotal) if payload.min_subtotal is not None else None,
        },
    )
    return DiscountRuleResponse.model_validate(row)


async def list_rules(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> list[DiscountRuleResponse]:
    stmt = select(DiscountRule).where(
        DiscountRule.tenant_id == tenant_id,
        DiscountRule.deleted_at.is_(None),
    )
    if store_id is not None:
        stmt = stmt.where(or_(DiscountRule.store_id == store_id, DiscountRule.store_id.is_(None)))
    if not include_inactive:
        stmt = stmt.where(DiscountRule.is_active.is_(True))
    stmt = stmt.order_by(DiscountRule.created_at)
    rows = (await session.execute(stmt)).scalars().all()
    return [DiscountRuleResponse.model_validate(r) for r in rows]


async def update_rule(
    session: AsyncSession,
    rule_id: uuid.UUID,
    payload: DiscountRuleUpdate,
    *,
    tenant_id: uuid.UUID,
) -> DiscountRuleResponse:
    row = await _load_rule(session, rule_id, tenant_id)
    before = row.is_active
    row.is_active = payload.is_active
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="discount_rule.updated",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("discount_rules", row.id),
        before={"is_active": before},
        after={"is_active": payload.is_active},
    )
    return DiscountRuleResponse.model_validate(row)


async def delete_rule(
    session: AsyncSession,
    rule_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    row = await _load_rule(session, rule_id, tenant_id)
    from datetime import UTC

    row.deleted_at = datetime.now(UTC)
    await session.flush()
    await audit(
        session,
        action="discount_rule.deleted",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("discount_rules", row.id),
        before={"name": row.name},
    )


async def _load_rule(
    session: AsyncSession, rule_id: uuid.UUID, tenant_id: uuid.UUID
) -> DiscountRule:
    row = (
        await session.execute(
            select(DiscountRule).where(
                DiscountRule.id == rule_id,
                DiscountRule.tenant_id == tenant_id,
                DiscountRule.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"discount rule {rule_id} not found",
            details={"rule_id": str(rule_id)},
        )
    return row


__all__ = [
    "active_rules_for_store",
    "best_rule",
    "create_rule",
    "delete_rule",
    "list_rules",
    "rule_discount",
    "rule_matches",
    "taipei_now",
    "update_rule",
]
