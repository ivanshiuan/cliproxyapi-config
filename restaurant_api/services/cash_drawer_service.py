"""交班/關帳 (cash drawer open/close, P7.2).

One row per till cycle (``cash_drawer_sessions`` — already in the schema
since Phase 1's closed-loop migration; this is the first slice to drive it).
Guided-closing flow: open with a known 備用金, and at close the server computes
``expected_close`` from real cash-payment rows in the window — the cashier
only ever enters what they *counted*, never what they expect. ``variance`` is
a DB-generated column (``closing_actual - expected_close``).

Scope: ``cash_sales`` sums every ``order_payments`` row with
``method=CASH`` and ``paid_at`` inside [opened_at, now) for the store. There is
no dedicated cash-refund flow yet (``OrderStatus.REFUNDED`` exists but nothing
writes a negative cash movement against it), so ``cash_refunds`` is honestly
reported as 0 rather than a number we can't actually derive.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..models import CashDrawerSession, Order, OrderPayment, OrderStatus, PaymentMethod
from ..schemas.cash_drawer import (
    CashDrawerSessionResponse,
    DrawerCloseRequest,
    DrawerCloseResult,
    DrawerExpectedBreakdown,
    DrawerOpenRequest,
)
from . import pos_order_service
from .audit_service import audit

_MONEY = Decimal("0.01")


async def _load_open(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    register_no: str | None,
) -> CashDrawerSession | None:
    return (
        await session.execute(
            select(CashDrawerSession).where(
                CashDrawerSession.tenant_id == tenant_id,
                CashDrawerSession.store_id == store_id,
                CashDrawerSession.register_no.is_(register_no)
                if register_no is None
                else CashDrawerSession.register_no == register_no,
                CashDrawerSession.closed_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def get_current(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    register_no: str | None = None,
) -> CashDrawerSessionResponse | None:
    row = await _load_open(session, tenant_id=tenant_id, store_id=store_id, register_no=register_no)
    return CashDrawerSessionResponse.model_validate(row) if row is not None else None


async def open_drawer(
    session: AsyncSession, payload: DrawerOpenRequest, *, tenant_id: uuid.UUID
) -> CashDrawerSessionResponse:
    existing = await _load_open(
        session, tenant_id=tenant_id, store_id=payload.store_id, register_no=payload.register_no
    )
    if existing is not None:
        raise ConflictError(
            message="這台收銀機已經開班了 — 請先關帳再開新的一班",
            details={"open_session_id": str(existing.id)},
        )
    row = CashDrawerSession(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        register_no=payload.register_no,
        opened_by=payload.opened_by,
        opening_float=payload.opening_float,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="cash_drawer.opened",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        actor_id=payload.opened_by,
        target=("cash_drawer_sessions", row.id),
        after={"opening_float": str(payload.opening_float)},
    )
    return CashDrawerSessionResponse.model_validate(row)


async def _expected_breakdown(
    session: AsyncSession, row: CashDrawerSession
) -> DrawerExpectedBreakdown:
    now = datetime.now(UTC)
    cash_rows = (
        await session.execute(
            select(OrderPayment)
            .join(Order, Order.id == OrderPayment.order_id)
            .where(
                Order.tenant_id == row.tenant_id,
                Order.store_id == row.store_id,
                OrderPayment.method == PaymentMethod.CASH,
                OrderPayment.paid_at >= row.opened_at,
                OrderPayment.paid_at <= now,
            )
        )
    ).scalars().all()
    cash_sales = sum((p.amount for p in cash_rows), Decimal("0"))
    cash_refunds = Decimal("0")  # honest zero — no cash-refund flow exists yet
    expected = (row.opening_float + cash_sales - cash_refunds).quantize(
        _MONEY, rounding=ROUND_HALF_UP
    )
    return DrawerExpectedBreakdown(
        opening_float=row.opening_float,
        cash_sales=cash_sales,
        cash_refunds=cash_refunds,
        expected_close=expected,
        order_count=len(cash_rows),
    )


async def preview_close(
    session: AsyncSession, drawer_session_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> DrawerExpectedBreakdown:
    """What the drawer should hold *right now* — the guided-closing checklist,
    called before the cashier counts so they know the target."""
    row = await _load_drawer(session, drawer_session_id, tenant_id)
    return await _expected_breakdown(session, row)


async def close_drawer(
    session: AsyncSession,
    drawer_session_id: uuid.UUID,
    payload: DrawerCloseRequest,
    *,
    tenant_id: uuid.UUID,
) -> DrawerCloseResult:
    row = await _load_drawer(session, drawer_session_id, tenant_id)
    if row.closed_at is not None:
        raise ConflictError(message="這班已經關過了", details={"session_id": str(row.id)})

    expected = await _expected_breakdown(session, row)
    now = datetime.now(UTC)
    row.closed_by = payload.closed_by
    row.closed_at = now
    row.expected_close = expected.expected_close
    row.closing_actual = payload.closing_actual
    row.notes = payload.notes
    await session.flush()
    await session.refresh(row)  # pulls the DB-generated `variance` column

    # 3-minute 關帳: daily P&L snapshot for the shift window, same discount +
    # 服務費 math the till used — reuses pos_order_service.amount_due so the
    # closing report can never drift from what checkout actually charged.
    closed_orders = (
        await session.execute(
            select(Order).where(
                Order.tenant_id == tenant_id,
                Order.store_id == row.store_id,
                Order.status == OrderStatus.CLOSED,
                Order.closed_at >= row.opened_at,
                Order.closed_at <= now,
            )
        )
    ).scalars().all()
    gross_total = Decimal("0")
    net_total = Decimal("0")
    charge_total = Decimal("0")
    for o in closed_orders:
        o = await _reload_with_relations(session, o.id, tenant_id)
        gross, _disc, charge, due = pos_order_service.amount_due(o)
        gross_total += gross
        net_total += due
        charge_total += charge

    await audit(
        session,
        action="cash_drawer.closed",
        tenant_id=tenant_id,
        store_id=row.store_id,
        actor_id=payload.closed_by,
        target=("cash_drawer_sessions", row.id),
        before={"expected_close": str(expected.expected_close)},
        after={
            "closing_actual": str(payload.closing_actual),
            "variance": str(row.variance),
        },
        reason=payload.notes,
    )
    return DrawerCloseResult(
        session=CashDrawerSessionResponse.model_validate(row),
        expected=expected,
        variance=row.variance if row.variance is not None else Decimal("0"),
        order_count=len(closed_orders),
        gross_sales=gross_total,
        discount_total=gross_total + charge_total - net_total,
        service_charge_total=charge_total,
        net_sales=net_total,
    )


async def _reload_with_relations(session: AsyncSession, order_id: uuid.UUID, tenant_id: uuid.UUID):
    from . import orders_service

    return await orders_service._load_order_with_relations(session, order_id, tenant_id)


async def _load_drawer(
    session: AsyncSession, drawer_session_id: uuid.UUID, tenant_id: uuid.UUID
) -> CashDrawerSession:
    row = (
        await session.execute(
            select(CashDrawerSession).where(
                CashDrawerSession.id == drawer_session_id,
                CashDrawerSession.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"cash drawer session {drawer_session_id} not found",
            details={"session_id": str(drawer_session_id)},
        )
    return row


__all__ = [
    "close_drawer",
    "get_current",
    "open_drawer",
    "preview_close",
]
