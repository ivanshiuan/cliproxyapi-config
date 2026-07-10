"""POS session ordering + cash checkout (P1.3b / P1.4).

Builds on the table-session flow: each seated party has exactly one *open*
order bound to its ``table_session_id``. This service owns the per-line POS
operations (add / change qty / 退菜) and the cash settlement that closes both
the order and the seating.

Design choices:
- Reuses ``orders_service`` internals (``_add_line_with_movement``,
  ``order_to_response``, ``_load_order_with_relations``, ``_compute_net_revenue``)
  so the BOM auto-deduct + KDS routing + response shaping stay identical to
  the ingest path — no divergence, no duplicated ledger logic.
- Unit price is snapshotted from the menu item server-side; the POS never
  sends a price (prevents till-side price tampering).
- 退菜 deletes the line and writes reversing stock movements (append-only
  ledger stays intact) plus an audit row — the removal is fully reconstructable.
- Cash checkout records an ``order_payments`` row, closes the order, and
  closes the table session in one transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models import (
    Ingredient,
    MenuItem,
    MovementType,
    Order,
    OrderChannel,
    OrderLine,
    OrderPayment,
    OrderStatus,
    OrderType,
    PaymentMethod,
    StockMovement,
    TableSession,
    TableSessionStatus,
)
from ..schemas.orders import OrderLineCreate, OrderResponse
from ..schemas.pos_orders import (
    CheckoutCashRequest,
    CheckoutResult,
    LineAddRequest,
    LineUpdateRequest,
    LineVoidRequest,
)
from . import orders_service
from .audit_service import audit

_TPE = ZoneInfo("Asia/Taipei")


# ──────────────────────────────────────────────────────────────────────────
# Session → order binding
# ──────────────────────────────────────────────────────────────────────────


async def _load_open_session(
    session: AsyncSession, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> TableSession:
    row = (
        await session.execute(
            select(TableSession).where(
                TableSession.id == session_id,
                TableSession.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"table session {session_id} not found",
            details={"session_id": str(session_id)},
        )
    if row.status != TableSessionStatus.OPEN:
        raise ConflictError(
            message=f"table session is {row.status.value}, not open",
            details={"current": row.status.value},
        )
    return row


async def _get_or_create_session_order(
    session: AsyncSession, ts: TableSession, *, tenant_id: uuid.UUID
) -> Order:
    """Return the open order bound to this session, creating one if absent."""
    existing = (
        await session.execute(
            select(Order).where(
                Order.table_session_id == ts.id,
                Order.status == OrderStatus.OPEN,
                Order.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    business_date = datetime.now(_TPE).date()
    order = Order(
        tenant_id=tenant_id,
        store_id=ts.store_id,
        order_no=f"POS-{ts.id.hex[:12]}",
        business_date=business_date,
        status=OrderStatus.OPEN,
        order_type=OrderType.DINE_IN,
        channel=OrderChannel.POS,
        table_session_id=ts.id,
    )
    session.add(order)
    await session.flush()
    return order


# ──────────────────────────────────────────────────────────────────────────
# Public operations
# ──────────────────────────────────────────────────────────────────────────


async def get_session_order(
    session: AsyncSession, session_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> OrderResponse:
    ts = await _load_open_session(session, session_id, tenant_id)
    order = await _get_or_create_session_order(session, ts, tenant_id=tenant_id)
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return orders_service.order_to_response(order)


async def add_line(
    session: AsyncSession,
    session_id: uuid.UUID,
    payload: LineAddRequest,
    *,
    tenant_id: uuid.UUID,
) -> OrderResponse:
    ts = await _load_open_session(session, session_id, tenant_id)
    order = await _get_or_create_session_order(session, ts, tenant_id=tenant_id)

    item = (
        await session.execute(
            select(MenuItem).where(
                MenuItem.id == payload.menu_item_id,
                MenuItem.tenant_id == tenant_id,
                MenuItem.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError(
            message=f"menu item {payload.menu_item_id} not found",
            details={"menu_item_id": str(payload.menu_item_id)},
        )

    line_payload = OrderLineCreate(
        menu_item_id=payload.menu_item_id,
        qty=payload.qty,
        unit_price=item.price,  # server-side price snapshot
        notes=payload.notes,
        kitchen_station=payload.kitchen_station,
    )
    now = datetime.now(UTC)
    placeholder: dict[str, Ingredient] = {}

    async def _get_placeholder() -> Ingredient:
        if "ing" not in placeholder:
            placeholder["ing"] = await orders_service._placeholder_ingredient(
                session, tenant_id, ts.store_id
            )
        return placeholder["ing"]

    await orders_service._add_line_with_movement(
        session=session,
        order=order,
        tenant_id=tenant_id,
        store_id=ts.store_id,
        line_payload=line_payload,
        get_placeholder=_get_placeholder,
        occurred_at=now,
    )
    await audit(
        session,
        action="pos_order.line_added",
        tenant_id=tenant_id,
        store_id=ts.store_id,
        actor_id=payload.actor_id,
        target=("orders", order.id),
        after={
            "menu_item_id": str(payload.menu_item_id),
            "qty": str(payload.qty),
            "unit_price": str(item.price),
        },
    )
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return orders_service.order_to_response(order)


async def update_line_qty(
    session: AsyncSession,
    line_id: uuid.UUID,
    payload: LineUpdateRequest,
    *,
    tenant_id: uuid.UUID,
) -> OrderResponse:
    line = await _load_line(session, line_id, tenant_id)
    order = await orders_service._load_order_with_relations(
        session, line.order_id, tenant_id
    )
    _guard_order_open(order)

    before_qty = line.qty
    line.qty = payload.qty
    line.line_total = payload.qty * line.unit_price
    await session.flush()
    await audit(
        session,
        action="pos_order.line_qty_changed",
        tenant_id=tenant_id,
        store_id=order.store_id,
        actor_id=payload.actor_id,
        target=("order_lines", line.id),
        before={"qty": str(before_qty)},
        after={"qty": str(payload.qty)},
    )
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return orders_service.order_to_response(order)


async def void_line(
    session: AsyncSession,
    line_id: uuid.UUID,
    payload: LineVoidRequest,
    *,
    tenant_id: uuid.UUID,
) -> OrderResponse:
    stub = await _load_line(session, line_id, tenant_id)
    order = await orders_service._load_order_with_relations(
        session, stub.order_id, tenant_id
    )
    _guard_order_open(order)
    # Grab the instance that lives in order.lines so removing it from the
    # collection triggers the delete-orphan cascade cleanly.
    line = next((ln for ln in order.lines if ln.id == line_id), None)
    if line is None:  # pragma: no cover - identity map guarantees presence
        raise NotFoundError(
            message=f"order line {line_id} not found",
            details={"line_id": str(line_id)},
        )

    # Reverse this line's stock consumption (append-only ledger: add inverse
    # rows, never mutate the originals).
    movements = (
        await session.execute(
            select(StockMovement).where(
                StockMovement.source_table == "order_lines",
                StockMovement.source_id == line.id,
                StockMovement.movement_type == MovementType.SALE_CONSUME,
            )
        )
    ).scalars().all()
    for mv in movements:
        session.add(
            StockMovement(
                tenant_id=order.tenant_id,
                store_id=mv.store_id,
                ingredient_id=mv.ingredient_id,
                movement_type=MovementType.ADJUSTMENT_IN,
                qty=-mv.qty,  # -(-x) = +x → restore stock
                source_table="order_line_voids",
                source_id=line.id,
                occurred_at=datetime.now(UTC),
                note=f"退菜 reversal of movement {mv.id}",
            )
        )

    snapshot = {
        "menu_item_id": str(line.menu_item_id),
        "qty": str(line.qty),
        "unit_price": str(line.unit_price),
    }
    order.lines.remove(line)  # delete-orphan cascade removes the row on flush
    await session.flush()
    await audit(
        session,
        action="pos_order.line_voided",
        tenant_id=tenant_id,
        store_id=order.store_id,
        actor_id=payload.actor_id,
        target=("orders", order.id),
        before=snapshot,
        reason=payload.reason,
    )
    return orders_service.order_to_response(order)


async def checkout_cash(
    session: AsyncSession,
    session_id: uuid.UUID,
    payload: CheckoutCashRequest,
    *,
    tenant_id: uuid.UUID,
) -> CheckoutResult:
    ts = await _load_open_session(session, session_id, tenant_id)
    order = (
        await session.execute(
            select(Order).where(
                Order.table_session_id == ts.id,
                Order.status == OrderStatus.OPEN,
                Order.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if order is None:
        raise ConflictError(
            message="no open order to check out for this session",
            details={"session_id": str(session_id)},
        )
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    if not order.lines:
        raise ValidationError("cannot check out an empty order")

    total = orders_service._compute_net_revenue(order)
    if payload.tendered < total:
        raise ValidationError(
            f"insufficient cash: tendered {payload.tendered} < total {total}"
        )
    change = payload.tendered - total

    now = datetime.now(UTC)
    session.add(
        OrderPayment(
            tenant_id=tenant_id,
            order_id=order.id,
            method=PaymentMethod.CASH,
            amount=total,
            fee_amount=Decimal("0"),
            paid_at=now,
        )
    )
    order.status = OrderStatus.CLOSED
    order.closed_at = now
    ts.status = TableSessionStatus.CLOSED
    ts.closed_at = now
    await session.flush()

    await audit(
        session,
        action="pos_order.checked_out_cash",
        tenant_id=tenant_id,
        store_id=order.store_id,
        actor_id=payload.actor_id,
        target=("orders", order.id),
        after={
            "total": str(total),
            "tendered": str(payload.tendered),
            "change": str(change),
        },
    )
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return CheckoutResult(
        order=orders_service.order_to_response(order),
        total=total,
        tendered=payload.tendered,
        change=change,
    )


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


async def _load_line(
    session: AsyncSession, line_id: uuid.UUID, tenant_id: uuid.UUID
) -> OrderLine:
    row = (
        await session.execute(
            select(OrderLine).where(
                OrderLine.id == line_id,
                OrderLine.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"order line {line_id} not found",
            details={"line_id": str(line_id)},
        )
    return row


def _guard_order_open(order: Order) -> None:
    if order.status != OrderStatus.OPEN:
        raise ConflictError(
            message=f"order is {order.status.value}, cannot modify lines",
            details={"current": order.status.value},
        )


__all__ = [
    "add_line",
    "checkout_cash",
    "get_session_order",
    "update_line_qty",
    "void_line",
]
