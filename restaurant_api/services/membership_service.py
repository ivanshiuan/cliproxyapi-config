"""會員生命週期引擎 — 把客人自動拉回店裡的 retention loop.

This is the automation layer that turns the *static* membership data
(``Customer.tier`` / ``lifetime_value`` / ``last_visit_at`` / ``birthdate``)
into a self-driving retention loop. Four levers, all idempotent so the
nightly job can re-run safely:

1. **Tier recompute** — re-grade every member off their **rolling 6-month**
   gross spend. The short window is deliberate: a member who hasn't been
   back in 6 months drops a tier, which is the hook the win-back push leans
   on ("再回來一次就保住金卡"). Source of truth is closed orders, not the
   cached ``lifetime_value`` (which is all-time).
2. **Welcome bonus** — first touch (wheel spin / first registration) drops
   joining points into the wallet so the prospect has an immediate reason to
   come in.
3. **Birthday gift** — 當日壽星自動進點 + 招待品項 (once per year).
4. **Dormant win-back** — sleepers past N days get a small comeback grant they
   can only spend *in store*, once per calendar period so we never spam.

Boundary rules (same as every other service):
- ``flush()`` only, never ``commit()`` — the caller (job / request DI) owns txn.
- Ledger stays append-only; corrections are new rows, never UPDATE/DELETE.
- ``tenant_id`` filters when supplied; ``None`` means *all tenants* (the
  nightly job runs estate-wide, tests scope to one tenant).

Idempotency is enforced through the ledger ``note`` marker per lever, so a
second run the same day/period is a no-op.

Tuning knobs (sensible Taiwan F&B starting values; adjust per brand):
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Customer,
    CustomerPointsLedger,
    CustomerTier,
    Order,
    OrderLine,
    OrderStatus,
)
from .audit_service import audit

logger = logging.getLogger("restaurant_api.services.membership")

_TPE = ZoneInfo("Asia/Taipei")

# ── Tuning knobs (documented; move to tenant_settings when per-brand needed) ──

# Rolling window the tier qualifies on. 6 months → "回來吃一次" cadence.
TIER_WINDOW_DAYS = 180

# Tier thresholds = rolling-window GROSS line revenue (before discounts, so a
# coupon never costs the member tier progress). Highest first.
_TIER_THRESHOLDS: list[tuple[CustomerTier, Decimal]] = [
    (CustomerTier.PLATINUM, Decimal("10000")),
    (CustomerTier.GOLD, Decimal("4000")),
    (CustomerTier.SILVER, Decimal("1500")),
    (CustomerTier.REGULAR, Decimal("0")),
]

WELCOME_POINTS = Decimal("100")
BIRTHDAY_POINTS = Decimal("200")
DORMANT_DAYS = 30
DORMANT_COMEBACK_POINTS = Decimal("150")


class TierChange(NamedTuple):
    """One member's tier transition (emitted so the caller can LINE-notify)."""

    customer: Customer
    old_tier: CustomerTier
    new_tier: CustomerTier

    @property
    def is_upgrade(self) -> bool:
        """True when the new tier outranks the old (worth a congrats push)."""
        order = list(CustomerTier)
        return order.index(self.new_tier) > order.index(self.old_tier)


def _tier_for_spend(spend: Decimal) -> CustomerTier:
    """Map a rolling-window spend figure to its qualifying tier."""
    for tier, threshold in _TIER_THRESHOLDS:
        if spend >= threshold:
            return tier
    return CustomerTier.REGULAR


def _tpe_today(now: datetime | None = None) -> date:
    """Asia/Taipei calendar date — the key for birthday / dormancy math."""
    return (now or datetime.now(UTC)).astimezone(_TPE).date()


async def _grant_once(
    session: AsyncSession,
    customer: Customer,
    *,
    delta: Decimal,
    reason: str,
    note: str,
) -> bool:
    """Grant points exactly once, race-safely. Returns ``True`` iff inserted.

    The insert runs inside a SAVEPOINT; the partial unique index on
    ``customer_points_ledger`` is the race-safe backstop, so a duplicate (already
    granted) surfaces as ``IntegrityError`` and is reported as a no-op — there's
    no check-then-insert window, so concurrent runs can't double-credit. (We use
    the index + ``IntegrityError`` rather than ``ON CONFLICT`` because the ledger
    carries an append-only DB RULE, which Postgres won't allow ON CONFLICT on.)
    """
    try:
        async with session.begin_nested():
            session.add(
                CustomerPointsLedger(
                    tenant_id=customer.tenant_id,
                    customer_id=customer.id,
                    delta=delta,
                    reason=reason,
                    note=note,
                )
            )
            await session.flush()
    except IntegrityError:
        return False
    customer.points_balance = (customer.points_balance or Decimal("0")) + delta
    return True


# ──────────────────────────────────────────────────────────────────────────
# 1. Tier recompute (rolling 6-month spend)
# ──────────────────────────────────────────────────────────────────────────


async def recompute_tiers(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    window_days: int = TIER_WINDOW_DAYS,
    now: datetime | None = None,
    actor_id: uuid.UUID | None = None,
) -> list[TierChange]:
    """Re-grade members off rolling-window gross spend; audit every change.

    Returns only the members whose tier actually moved (so the caller can push
    a congrats message on upgrades and skip the no-ops).
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=window_days)

    spend_stmt = (
        select(
            Order.customer_id,
            func.coalesce(func.sum(OrderLine.line_total), Decimal("0")),
        )
        .join(OrderLine, OrderLine.order_id == Order.id)
        .where(
            Order.status == OrderStatus.CLOSED,
            Order.closed_at >= cutoff,
            Order.customer_id.is_not(None),
            Order.deleted_at.is_(None),
        )
        .group_by(Order.customer_id)
    )
    if tenant_id is not None:
        spend_stmt = spend_stmt.where(Order.tenant_id == tenant_id)
    spend_by_customer: dict[uuid.UUID, Decimal] = {
        cid: Decimal(total)
        for cid, total in (await session.execute(spend_stmt)).all()
        if cid is not None
    }

    cust_stmt = select(Customer).where(Customer.deleted_at.is_(None))
    if tenant_id is not None:
        cust_stmt = cust_stmt.where(Customer.tenant_id == tenant_id)
    customers = (await session.execute(cust_stmt)).scalars().all()

    changes: list[TierChange] = []
    for customer in customers:
        spend = spend_by_customer.get(customer.id, Decimal("0"))
        target = _tier_for_spend(spend)
        if target == customer.tier:
            continue
        old = customer.tier
        customer.tier = target
        changes.append(TierChange(customer=customer, old_tier=old, new_tier=target))
        await audit(
            session,
            action="membership.tier_changed",
            tenant_id=customer.tenant_id,
            actor_id=actor_id,
            target=("customers", customer.id),
            before={"tier": old.value},
            after={
                "tier": target.value,
                "window_spend": str(spend),
                "window_days": window_days,
            },
            reason="nightly tier recompute",
        )
    if changes:
        await session.flush()
    return changes


# ──────────────────────────────────────────────────────────────────────────
# 2. Welcome bonus (first touch — wheel spin / first registration)
# ──────────────────────────────────────────────────────────────────────────


async def grant_welcome_bonus(
    session: AsyncSession,
    customer: Customer,
    *,
    points: Decimal = WELCOME_POINTS,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Drop joining points into a brand-new member's wallet (idempotent).

    No-op (returns ``False``) if the member already received a welcome bonus —
    enforced atomically by the DB, so calling it on every spin is safe.
    """
    granted = await _grant_once(
        session,
        customer,
        delta=points,
        reason="welcome.signup",
        note="welcome bonus on join",
    )
    if not granted:
        return False
    await session.flush()
    await audit(
        session,
        action="membership.welcome_granted",
        tenant_id=customer.tenant_id,
        actor_id=actor_id,
        target=("customers", customer.id),
        after={"delta": str(points), "reason": "welcome.signup"},
    )
    return True


# ──────────────────────────────────────────────────────────────────────────
# 3. Birthday gift (當日壽星)
# ──────────────────────────────────────────────────────────────────────────


async def grant_birthday_gifts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    points: Decimal = BIRTHDAY_POINTS,
    now: datetime | None = None,
) -> list[Customer]:
    """Grant a birthday point gift to every member whose birthday is today.

    Idempotent within the year via a ``birthday.gift`` ledger marker keyed to
    the calendar year. Returns the members gifted (so the caller pushes LINE).
    """
    today = _tpe_today(now)
    stmt = select(Customer).where(
        Customer.deleted_at.is_(None),
        Customer.birthdate.is_not(None),
        func.extract("month", Customer.birthdate) == today.month,
        func.extract("day", Customer.birthdate) == today.day,
    )
    if tenant_id is not None:
        stmt = stmt.where(Customer.tenant_id == tenant_id)
    candidates = (await session.execute(stmt)).scalars().all()

    marker = f"birthday.gift:{today.year}"
    gifted: list[Customer] = []
    for customer in candidates:
        granted = await _grant_once(
            session,
            customer,
            delta=points,
            reason="birthday.gift",
            note=marker,
        )
        if not granted:
            continue
        gifted.append(customer)
        await audit(
            session,
            action="membership.birthday_gift",
            tenant_id=customer.tenant_id,
            target=("customers", customer.id),
            after={"delta": str(points), "year": today.year},
            reason="birthday gift batch",
        )
    if gifted:
        await session.flush()
    return gifted


# ──────────────────────────────────────────────────────────────────────────
# 4. Dormant win-back (沉睡喚回)
# ──────────────────────────────────────────────────────────────────────────


async def grant_dormant_winback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    dormant_days: int = DORMANT_DAYS,
    points: Decimal = DORMANT_COMEBACK_POINTS,
    now: datetime | None = None,
) -> list[Customer]:
    """Grant a small comeback bonus to sleepers past ``dormant_days``.

    The bonus is spendable only by visiting — that's the hook. Idempotent per
    calendar month via a ``winback.dormant`` marker, so a sleeper is nudged at
    most once a month, never daily. Returns the members nudged (for LINE push).
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=dormant_days)
    today = _tpe_today(now)

    stmt = select(Customer).where(
        Customer.deleted_at.is_(None),
        Customer.last_visit_at.is_not(None),
        Customer.last_visit_at < cutoff,
    )
    if tenant_id is not None:
        stmt = stmt.where(Customer.tenant_id == tenant_id)
    candidates = (await session.execute(stmt)).scalars().all()

    marker = f"winback.dormant:{today.year}-{today.month:02d}"
    nudged: list[Customer] = []
    for customer in candidates:
        granted = await _grant_once(
            session,
            customer,
            delta=points,
            reason="winback.dormant",
            note=marker,
        )
        if not granted:
            continue
        nudged.append(customer)
        await audit(
            session,
            action="membership.winback_granted",
            tenant_id=customer.tenant_id,
            target=("customers", customer.id),
            after={"delta": str(points), "dormant_days": dormant_days},
            reason="dormant win-back batch",
        )
    if nudged:
        await session.flush()
    return nudged


__all__ = [
    "BIRTHDAY_POINTS",
    "DORMANT_COMEBACK_POINTS",
    "DORMANT_DAYS",
    "TIER_WINDOW_DAYS",
    "WELCOME_POINTS",
    "TierChange",
    "grant_birthday_gifts",
    "grant_dormant_winback",
    "grant_welcome_bonus",
    "recompute_tiers",
]
