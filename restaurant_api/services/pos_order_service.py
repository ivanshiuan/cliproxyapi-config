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
from ..api.pos_auth import PosPrincipal
from ..models import (
    DiscountKind,
    Ingredient,
    MenuItem,
    MovementType,
    Order,
    OrderChannel,
    OrderDiscount,
    OrderLine,
    OrderPayment,
    OrderStatus,
    OrderType,
    PaymentMethod,
    StockMovement,
    TableSession,
    TableSessionStatus,
)
from ..models.base import uuid7
from ..schemas.orders import OrderLineCreate, OrderResponse
from ..schemas.pos_orders import (
    CheckoutCashRequest,
    CheckoutQuote,
    CheckoutResult,
    DiscountApplyRequest,
    LineAddRequest,
    LineUpdateRequest,
    LineVoidRequest,
    PartialPayRequest,
    PartialPayResult,
    ServiceChargeRequest,
    TakeoutSaleRequest,
)
from . import orders_service, pos_auth_service
from .audit_service import audit
from .event_hub import record_event
from .permissions import SensitiveAction

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
    session: AsyncSession,
    ts: TableSession,
    *,
    tenant_id: uuid.UUID,
    channel: OrderChannel = OrderChannel.POS,
) -> Order:
    """Return the open order bound to this session, creating one if absent.

    ``channel`` marks who *opened* the order (POS clerk vs table-QR customer);
    a session only ever has one open order, so later adds from the other side
    join the same bill.
    """
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
        channel=channel,
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
    channel: OrderChannel = OrderChannel.POS,
) -> OrderResponse:
    ts = await _load_open_session(session, session_id, tenant_id)
    order = await _get_or_create_session_order(
        session, ts, tenant_id=tenant_id, channel=channel
    )

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
        # POS/table-QR lines default onto the KDS board (station "kitchen");
        # the bulk /orders ingest path keeps its explicit opt-in behaviour.
        kitchen_station=payload.kitchen_station or "kitchen",
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
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=ts.store_id,
        event_type="order.line_added",
        table_id=ts.table_id,
        session_id=ts.id,
        payload={"order_id": str(order.id), "menu_item_id": str(payload.menu_item_id)},
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
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=order.store_id,
        event_type="order.line_updated",
        session_id=order.table_session_id,
        payload={"order_id": str(order.id), "line_id": str(line.id)},
    )
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return orders_service.order_to_response(order)


async def void_line(
    session: AsyncSession,
    line_id: uuid.UUID,
    payload: LineVoidRequest,
    *,
    tenant_id: uuid.UUID,
    principal: PosPrincipal | None = None,
) -> OrderResponse:
    stub = await _load_line(session, line_id, tenant_id)
    order = await orders_service._load_order_with_relations(
        session, stub.order_id, tenant_id
    )
    _guard_order_open(order)
    # 退菜 is a gated action: the actor's role must hold VOID_LINE, or a manager
    # authorizes inline. Raises 403 otherwise.
    actor_id, via = await pos_auth_service.authorize_sensitive(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action=SensitiveAction.VOID_LINE,
        override_employee_id=payload.override.employee_id if payload.override else None,
        override_pin=payload.override.pin if payload.override else None,
    )
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
        actor_id=actor_id,  # who authorized (self or the override manager)
        target=("orders", order.id),
        before=snapshot,
        after={"authorized_via": via},
        reason=payload.reason,
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=order.store_id,
        event_type="order.line_voided",
        session_id=order.table_session_id,
        payload={"order_id": str(order.id), "line_id": str(line_id)},
    )
    return orders_service.order_to_response(order)


async def apply_discount(
    session: AsyncSession,
    session_id: uuid.UUID,
    payload: DiscountApplyRequest,
    *,
    tenant_id: uuid.UUID,
    principal: PosPrincipal | None = None,
) -> OrderResponse:
    """折扣 — attach a percent/amount discount to the session's open order.

    Gated like 退菜. The discount row flows into ``_compute_net_revenue`` at
    checkout automatically (same stack the till uses), so no separate total math.
    """
    ts = await _load_open_session(session, session_id, tenant_id)
    order = await _get_or_create_session_order(session, ts, tenant_id=tenant_id)
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    _guard_order_open(order)
    if not order.lines:
        raise ValidationError("空訂單不能套用折扣")

    actor_id, via = await pos_auth_service.authorize_sensitive(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action=SensitiveAction.APPLY_DISCOUNT,
        override_employee_id=payload.override.employee_id if payload.override else None,
        override_pin=payload.override.pin if payload.override else None,
    )

    session.add(
        OrderDiscount(
            tenant_id=tenant_id,
            order_id=order.id,
            kind=DiscountKind(payload.kind),
            value=payload.value,
            reason=payload.reason,
            applied_by=actor_id,
        )
    )
    await session.flush()
    await audit(
        session,
        action="pos_order.discount_applied",
        tenant_id=tenant_id,
        store_id=order.store_id,
        actor_id=actor_id,
        target=("orders", order.id),
        after={"kind": payload.kind, "value": str(payload.value), "authorized_via": via},
        reason=payload.reason,
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=order.store_id,
        event_type="order.discount_applied",
        table_id=ts.table_id,
        session_id=ts.id,
        payload={"order_id": str(order.id)},
    )
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return orders_service.order_to_response(order)


_MONEY_Q = Decimal("0.01")


def amount_due(order: Order) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Single source of the till math: (gross, discount_total, service_charge, due).

    due = discount-stacked net + 服務費 (rate x gross). Quote, checkout,
    partial payments, and the sales report all call THIS — one number,
    zero divergence.
    """
    gross = sum((ln.line_total for ln in order.lines), Decimal("0"))
    discounted = orders_service._compute_net_revenue(order)
    charge = (gross * (order.service_charge_rate or Decimal("0"))).quantize(_MONEY_Q)
    return gross, gross - discounted, charge, discounted + charge


def _paid_so_far(order: Order) -> Decimal:
    return sum((p.amount for p in order.payments), Decimal("0"))


def _build_quote(order: Order) -> CheckoutQuote:
    gross, discount_total, charge, due = amount_due(order)
    paid = _paid_so_far(order)
    return CheckoutQuote(
        gross=gross,
        discount_total=discount_total,
        service_charge=charge,
        net=due,
        paid=paid,
        remaining=due - paid,
    )


async def quote(
    session: AsyncSession,
    session_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> CheckoutQuote:
    """Amount due for the open order (discounts + 服務費 + partial payments
    applied) — checkout UI reads this instead of doing money math client-side."""
    ts = await _load_open_session(session, session_id, tenant_id)
    order = await _get_or_create_session_order(session, ts, tenant_id=tenant_id)
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return _build_quote(order)


async def set_service_charge(
    session: AsyncSession,
    session_id: uuid.UUID,
    payload: ServiceChargeRequest,
    *,
    tenant_id: uuid.UUID,
) -> CheckoutQuote:
    """Set/clear the 服務費 rate on the session's open order (0.1 = 10%)."""
    ts = await _load_open_session(session, session_id, tenant_id)
    order = await _get_or_create_session_order(session, ts, tenant_id=tenant_id)
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    _guard_order_open(order)
    before = order.service_charge_rate
    order.service_charge_rate = payload.rate
    await session.flush()
    await audit(
        session,
        action="pos_order.service_charge_set",
        tenant_id=tenant_id,
        store_id=order.store_id,
        actor_id=payload.actor_id,
        target=("orders", order.id),
        before={"rate": str(before)},
        after={"rate": str(payload.rate)},
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=order.store_id,
        event_type="order.service_charge_set",
        table_id=ts.table_id,
        session_id=ts.id,
        payload={"order_id": str(order.id), "rate": str(payload.rate)},
    )
    return _build_quote(order)


async def pay_partial(
    session: AsyncSession,
    session_id: uuid.UUID,
    payload: PartialPayRequest,
    *,
    tenant_id: uuid.UUID,
) -> PartialPayResult:
    """拆單/分開結帳 — record a cash payment for part of the bill.

    When the running payments cover the amount due, the order and the seating
    close automatically (same as a full checkout).
    """
    ts = await _load_open_session(session, session_id, tenant_id)
    order = await _get_or_create_session_order(session, ts, tenant_id=tenant_id)
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    _guard_order_open(order)
    if not order.lines:
        raise ValidationError("空訂單不能收款")

    _gross, _disc, _charge, due = amount_due(order)
    remaining = due - _paid_so_far(order)
    if payload.amount > remaining:
        raise ValidationError(
            f"收款金額 {payload.amount} 超過剩餘應收 {remaining}",
            details={"remaining": str(remaining)},
        )
    if payload.tendered < payload.amount:
        raise ValidationError(
            f"insufficient cash: tendered {payload.tendered} < amount {payload.amount}"
        )
    change = payload.tendered - payload.amount

    now = datetime.now(UTC)
    session.add(
        OrderPayment(
            tenant_id=tenant_id,
            order_id=order.id,
            method=PaymentMethod.CASH,
            amount=payload.amount,
            fee_amount=Decimal("0"),
            paid_at=now,
        )
    )
    remaining_after = remaining - payload.amount
    closed = remaining_after <= Decimal("0")
    if closed:
        order.status = OrderStatus.CLOSED
        order.closed_at = now
        ts.status = TableSessionStatus.CLOSED
        ts.closed_at = now
    await session.flush()

    await audit(
        session,
        action="pos_order.partial_payment",
        tenant_id=tenant_id,
        store_id=order.store_id,
        actor_id=payload.actor_id,
        target=("orders", order.id),
        after={
            "amount": str(payload.amount),
            "remaining": str(remaining_after),
            "closed": closed,
        },
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=order.store_id,
        event_type="order.checkout" if closed else "order.partial_payment",
        table_id=ts.table_id,
        session_id=ts.id,
        payload={"order_id": str(order.id), "remaining": str(remaining_after)},
    )
    return PartialPayResult(
        paid_amount=payload.amount,
        change=change,
        remaining=remaining_after,
        closed=closed,
    )


async def takeout_sale(
    session: AsyncSession,
    payload: TakeoutSaleRequest,
    *,
    tenant_id: uuid.UUID,
) -> CheckoutResult:
    """外帶快速單 (iCHEF 快速結帳): build + pay + close in one transaction.

    No table session — the order stands alone as TAKEOUT/POS. Lines take the
    server-side menu price snapshot and run the same BOM/KDS path as dine-in;
    cash settles immediately and the receipt (with change) comes back in one
    round trip. Insufficient cash rolls the whole thing back (single txn).
    """
    ts_now = datetime.now(UTC)
    order = Order(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        order_no=f"TO-{uuid7().hex[:12]}",
        business_date=datetime.now(_TPE).date(),
        status=OrderStatus.OPEN,
        order_type=OrderType.TAKEOUT,
        channel=OrderChannel.POS,
    )
    session.add(order)
    await session.flush()

    placeholder: dict[str, Ingredient] = {}

    async def _get_placeholder() -> Ingredient:
        if "ing" not in placeholder:
            placeholder["ing"] = await orders_service._placeholder_ingredient(
                session, tenant_id, payload.store_id
            )
        return placeholder["ing"]

    for item_req in payload.items:
        item = (
            await session.execute(
                select(MenuItem).where(
                    MenuItem.id == item_req.menu_item_id,
                    MenuItem.tenant_id == tenant_id,
                    MenuItem.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if item is None:
            raise NotFoundError(
                message=f"menu item {item_req.menu_item_id} not found",
                details={"menu_item_id": str(item_req.menu_item_id)},
            )
        await orders_service._add_line_with_movement(
            session=session,
            order=order,
            tenant_id=tenant_id,
            store_id=payload.store_id,
            line_payload=OrderLineCreate(
                menu_item_id=item_req.menu_item_id,
                qty=item_req.qty,
                unit_price=item.price,  # server-side snapshot
                notes=item_req.notes,
                kitchen_station="kitchen",  # takeout still cooks
            ),
            get_placeholder=_get_placeholder,
            occurred_at=ts_now,
        )

    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    total = orders_service._compute_net_revenue(order)
    if payload.tendered < total:
        raise ValidationError(
            f"insufficient cash: tendered {payload.tendered} < total {total}"
        )
    change = payload.tendered - total

    session.add(
        OrderPayment(
            tenant_id=tenant_id,
            order_id=order.id,
            method=PaymentMethod.CASH,
            amount=total,
            fee_amount=Decimal("0"),
            paid_at=ts_now,
        )
    )
    order.status = OrderStatus.CLOSED
    order.closed_at = ts_now
    await session.flush()

    await audit(
        session,
        action="pos_order.takeout_sale",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        actor_id=payload.actor_id,
        target=("orders", order.id),
        after={
            "total": str(total),
            "tendered": str(payload.tendered),
            "change": str(change),
            "items": len(payload.items),
        },
    )
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=payload.store_id,
        event_type="order.takeout_sale",
        payload={"order_id": str(order.id), "total": str(total)},
    )
    order = await orders_service._load_order_with_relations(session, order.id, tenant_id)
    return CheckoutResult(
        order=orders_service.order_to_response(order),
        total=total,
        tendered=payload.tendered,
        change=change,
    )


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

    # Settle whatever is still owed: discount stack + 服務費 minus partial payments.
    _gross, _disc, _charge, due = amount_due(order)
    total = due - _paid_so_far(order)
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
    await record_event(
        session,
        tenant_id=tenant_id,
        store_id=order.store_id,
        event_type="order.checkout",
        table_id=ts.table_id,
        session_id=ts.id,
        payload={"order_id": str(order.id), "total": str(total)},
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
    "amount_due",
    "apply_discount",
    "checkout_cash",
    "get_session_order",
    "pay_partial",
    "quote",
    "set_service_charge",
    "takeout_sale",
    "update_line_qty",
    "void_line",
]
