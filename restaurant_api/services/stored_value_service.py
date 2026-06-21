"""儲值雙倍 — stored-value (prepaid wallet) service.

The single highest-ROI loyalty mechanic in Taiwan F&B: the customer prepays,
you book the cash now, and a top-up bonus (儲 1000 送 200) creates sunk-cost
lock-in that pulls them back to spend it only at your store.

Boundaries (same as every other service):
- ``flush()`` only, never ``commit()`` — the DI/job layer owns the transaction.
- ``customer_stored_value_ledger`` is append-only (DB RULE blocks UPDATE/DELETE);
  every top-up / bonus / spend / refund is a new signed row. The cached running
  total lives on ``Customer.stored_value_balance``.
- Concurrency: the customer row is locked ``FOR UPDATE`` before a balance change
  so two cashier terminals can't both pass the balance check and overspend.

Bonus schedule is a tunable constant here until a ``tenant_settings`` table lets
operators set it per brand.
"""

from __future__ import annotations

import contextlib
import uuid
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..integrations.line import LineMessage, LineMessenger, get_messenger
from ..models import Customer, CustomerStoredValueLedger
from ..schemas.stored_value import (
    StoredValueBalanceResponse,
    StoredValueLedgerResponse,
    StoredValueSpendRequest,
    StoredValueTopUpRequest,
)
from .audit_service import audit

# Top-up bonus schedule: (門檻, 加贈比例). Highest threshold first. 儲越多送越多.
_BONUS_TIERS: list[tuple[Decimal, Decimal]] = [
    (Decimal("3000"), Decimal("0.25")),
    (Decimal("1000"), Decimal("0.20")),
    (Decimal("500"), Decimal("0.10")),
]


def bonus_for(amount: Decimal) -> Decimal:
    """Promotional bonus for a top-up amount, rounded DOWN to whole TWD.

    Round-down is deliberate — never over-issue stored-value liability.
    """
    for threshold, rate in _BONUS_TIERS:
        if amount >= threshold:
            return (amount * rate).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return Decimal("0")


async def _lock_customer(
    session: AsyncSession, customer_id: uuid.UUID, tenant_id: uuid.UUID
) -> Customer:
    """Load + row-lock a live customer, scoped to tenant."""
    row = (
        await session.execute(
            select(Customer)
            .where(Customer.id == customer_id, Customer.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"customer {customer_id} not found",
            details={"customer_id": str(customer_id)},
        )
    if row.deleted_at is not None:
        raise ConflictError(
            message="cannot operate on a deleted customer",
            details={"customer_id": str(customer_id)},
        )
    return row


async def top_up(
    session: AsyncSession,
    customer_id: uuid.UUID,
    payload: StoredValueTopUpRequest,
    *,
    tenant_id: uuid.UUID,
    messenger: LineMessenger | None = None,
) -> StoredValueBalanceResponse:
    """Top up the wallet: book the paid amount + any promotional bonus.

    Writes the paid amount and the bonus as **two** ledger rows (so the bonus
    liability is separable in analytics), bumps the cached balance, audits, and
    pushes a LINE confirmation when the member is reachable.
    """
    customer = await _lock_customer(session, customer_id, tenant_id)

    amount = payload.amount
    bonus = bonus_for(amount)
    before = customer.stored_value_balance or Decimal("0")

    session.add(
        CustomerStoredValueLedger(
            tenant_id=tenant_id,
            customer_id=customer.id,
            delta=amount,
            reason="topup",
            note=payload.note,
        )
    )
    if bonus > 0:
        session.add(
            CustomerStoredValueLedger(
                tenant_id=tenant_id,
                customer_id=customer.id,
                delta=bonus,
                reason="topup.bonus",
                note=f"top-up bonus on {amount}",
            )
        )
    customer.stored_value_balance = before + amount + bonus
    await session.flush()

    await audit(
        session,
        action="customer.stored_value_topup",
        tenant_id=tenant_id,
        actor_id=payload.actor_id,
        target=("customers", customer.id),
        before={"stored_value_balance": str(before)},
        after={
            "stored_value_balance": str(customer.stored_value_balance),
            "amount": str(amount),
            "bonus": str(bonus),
        },
        reason="stored value top-up",
    )

    out = messenger or get_messenger()
    if customer.line_user_id:
        bonus_line = f"加贈 {bonus} 元\n" if bonus > 0 else ""
        with contextlib.suppress(Exception):  # best-effort side channel
            await out.push(
                customer.line_user_id,
                LineMessage(
                    kind="text",
                    text=(
                        f"儲值成功!\n本次儲值 {amount} 元\n{bonus_line}"
                        f"目前餘額 {customer.stored_value_balance} 元"
                    ),
                ),
            )

    return StoredValueBalanceResponse(
        customer_id=customer.id, stored_value_balance=customer.stored_value_balance
    )


async def spend(
    session: AsyncSession,
    customer_id: uuid.UUID,
    payload: StoredValueSpendRequest,
    *,
    tenant_id: uuid.UUID,
) -> StoredValueBalanceResponse:
    """Pay with stored value — writes a NEGATIVE row, guarded against overspend."""
    customer = await _lock_customer(session, customer_id, tenant_id)

    amount = payload.amount
    before = customer.stored_value_balance or Decimal("0")
    if before < amount:
        raise ConflictError(
            message="insufficient stored-value balance",
            details={"requested": str(amount), "available": str(before)},
        )

    session.add(
        CustomerStoredValueLedger(
            tenant_id=tenant_id,
            customer_id=customer.id,
            delta=-amount,
            reason="spend",
            source_order_id=payload.source_order_id,
            note=payload.note,
        )
    )
    customer.stored_value_balance = before - amount
    await session.flush()

    await audit(
        session,
        action="customer.stored_value_spend",
        tenant_id=tenant_id,
        actor_id=payload.actor_id,
        target=("customers", customer.id),
        before={"stored_value_balance": str(before)},
        after={
            "stored_value_balance": str(customer.stored_value_balance),
            "amount": str(amount),
        },
        reason="stored value spend",
    )
    return StoredValueBalanceResponse(
        customer_id=customer.id, stored_value_balance=customer.stored_value_balance
    )


async def get_balance(
    session: AsyncSession, customer_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> StoredValueBalanceResponse:
    """Read the cached wallet balance."""
    customer = (
        await session.execute(
            select(Customer).where(
                Customer.id == customer_id, Customer.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if customer is None:
        raise NotFoundError(
            message=f"customer {customer_id} not found",
            details={"customer_id": str(customer_id)},
        )
    return StoredValueBalanceResponse(
        customer_id=customer.id, stored_value_balance=customer.stored_value_balance
    )


async def list_ledger(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    limit: int = 200,
) -> list[StoredValueLedgerResponse]:
    """Read-only stored-value history, newest first."""
    stmt = (
        select(CustomerStoredValueLedger)
        .where(
            CustomerStoredValueLedger.customer_id == customer_id,
            CustomerStoredValueLedger.tenant_id == tenant_id,
        )
        .order_by(
            CustomerStoredValueLedger.created_at.desc(),
            CustomerStoredValueLedger.id.desc(),
        )
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [StoredValueLedgerResponse.model_validate(r) for r in rows]


__all__ = [
    "bonus_for",
    "get_balance",
    "list_ledger",
    "spend",
    "top_up",
]
