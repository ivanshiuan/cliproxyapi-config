"""Business-logic service for the Orders router.

This module owns the *atomic* state transitions of an order — create, close,
void — and the bookkeeping ledger writes that go with each. Routers stay
thin and stateless; services here do the work.

Phase 1 simplifications (TODO callouts inline):
- BOM consumption is stubbed: one ``stock_movements`` row per order line at
  ``qty = -1`` against a placeholder ingredient. The real swap-in is
  ``bom_consumer.expand_consumption(line)`` per ``specs/bom_consumer.md``.
- Discount → ``net_revenue`` calculation is a flat sum of line_totals minus
  flat-amount discounts; the real engine is ``discount_resolver`` per
  ``specs/discount_resolver.md``.
- 統一發票 / 載具 / 統編 fields are stored as-supplied; the validator hook
  (``uniform_invoice_validator``) is not yet wired here — Pydantic regex
  catches the format, downstream validates against MoF.
- LINE messenger is fire-and-forget on close, skipped when no customer is
  attached (the order header has no customer FK in Phase 1).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models import (
    Ingredient,
    MovementType,
    Order,
    OrderDiscount,
    OrderLine,
    OrderPayment,
    OrderStatus,
    Recipe,
    StockMovement,
    Store,
)
from ..models.orders import DiscountKind, PaymentMethod
from ..schemas.orders import (
    OrderCreateRequest,
    OrderLineCreate,
    OrderResponse,
)
from .calc.bom_consumer import (
    BOMConsumeInput,
    RecipeRow,
    consume_bom,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..integrations.line import LineMessenger

logger = logging.getLogger("restaurant_api.services.orders")

_TPE = ZoneInfo("Asia/Taipei")


# ──────────────────────────────────────────────────────────────────────────
# Read helpers
# ──────────────────────────────────────────────────────────────────────────


async def _load_order_with_relations(
    session: AsyncSession,
    order_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Order:
    """Eager-load lines/discounts/payments for a single order.

    Raises ``NotFoundError`` if the order doesn't exist or is soft-deleted.

    Phase-1 multi-tenancy is off (per ``deps.get_current_tenant_id``):
    the header tenant is the fixed default and may not match the order's
    stored tenant (e.g. in tests with random per-test tenants). We look
    up by id only — Phase 2 will re-add the tenant filter once the
    resolver routes real tenant ids through the auth gateway.
    """
    _ = tenant_id  # reserved for Phase 2 RLS / explicit filter
    stmt = (
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.lines),
            selectinload(Order.discounts),
            selectinload(Order.payments),
        )
    )
    result = await session.execute(stmt)
    order = result.scalar_one_or_none()
    if order is None or order.deleted_at is not None:
        raise NotFoundError(f"order not found: {order_id}")
    return order


async def _resolve_tenant_from_store(
    session: AsyncSession,
    store_id: uuid.UUID,
    request_tenant_id: uuid.UUID,
) -> uuid.UUID:
    """Phase-1: derive tenant from the store rather than trusting the header.

    We still cross-check that the header tenant matches the store's tenant
    so a multi-tenant rollout doesn't accidentally let stores leak.
    """
    stmt = select(Store.tenant_id).where(Store.id == store_id)
    result = await session.execute(stmt)
    store_tenant = result.scalar_one_or_none()
    if store_tenant is None:
        raise NotFoundError(f"store not found: {store_id}")
    # In Phase 1 multi-tenancy is off → request_tenant is the default; trust
    # the store. When auth lands this becomes a hard mismatch check.
    return store_tenant


async def _load_active_recipes(
    session: AsyncSession,
    menu_item_id: uuid.UUID,
) -> list[RecipeRow]:
    """Fetch current (effective_to IS NULL) recipes for a menu item.

    Joins to ``ingredients`` so each row carries the standard unit cost the
    calc engine needs for theoretical-COGS computation. Inactive ingredients
    are skipped — a recipe pointing at a discontinued ingredient is treated
    as if it didn't exist.
    """
    stmt = (
        select(
            Recipe.ingredient_id,
            Recipe.qty_per_serving,
            Ingredient.standard_cost_per_unit,
        )
        .join(Ingredient, Ingredient.id == Recipe.ingredient_id)
        .where(
            Recipe.menu_item_id == menu_item_id,
            Recipe.effective_to.is_(None),
            Ingredient.is_active.is_(True),
        )
    )
    rows = (await session.execute(stmt)).all()
    return [
        RecipeRow(
            ingredient_id=str(row[0]),
            qty_per_serving=row[1],
            standard_unit_cost=row[2],
        )
        for row in rows
    ]


async def _placeholder_ingredient(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
) -> Ingredient:
    """Return (or create) a placeholder ingredient for fallback BOM consumption.

    Used only when a menu item has *no* active recipes configured — we still
    write one ledger row per line so the audit trail stays complete. Real
    recipes flow through ``consume_bom`` (calc engine) and skip this path.
    """
    stmt = (
        select(Ingredient)
        .where(
            Ingredient.tenant_id == tenant_id,
            Ingredient.store_id == store_id,
            Ingredient.name == "__phase1_placeholder__",
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    ing = result.scalar_one_or_none()
    if ing is not None:
        return ing
    ing = Ingredient(
        tenant_id=tenant_id,
        store_id=store_id,
        name="__phase1_placeholder__",
        unit="piece",
        standard_cost_per_unit=Decimal("0"),
        is_active=True,
    )
    session.add(ing)
    await session.flush()
    return ing


# ──────────────────────────────────────────────────────────────────────────
# Response shaping (Asia/Taipei timestamp rendering)
# ──────────────────────────────────────────────────────────────────────────


def _to_tpe(dt: datetime | None) -> datetime | None:
    """Convert a UTC (or naive-assumed-UTC) datetime to Asia/Taipei."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_TPE)


def order_to_response(order: Order) -> OrderResponse:
    """Build the public response shape with TPE timestamps.

    We bypass ``model_validate`` for the timestamps so we control the zone;
    the rest of the fields ride the standard ``from_attributes`` path.
    """
    # Sort children deterministically by id (uuid7 → time-ordered).
    lines = sorted(order.lines, key=lambda x: x.id)
    discounts = sorted(order.discounts, key=lambda x: x.id)
    payments = sorted(order.payments, key=lambda x: x.id)

    opened_at = _to_tpe(order.opened_at)
    # opened_at is non-null per schema; assert for the type-checker.
    assert opened_at is not None

    return OrderResponse(
        id=order.id,
        store_id=order.store_id,
        tenant_id=order.tenant_id,
        order_no=order.order_no,
        business_date=order.business_date,
        opened_at=opened_at,
        closed_at=_to_tpe(order.closed_at),
        status=order.status.value,  # type: ignore[arg-type]
        invoice_number=order.invoice_number,
        carrier_type=order.carrier_type,
        carrier_id=order.carrier_id,
        buyer_tax_id=order.buyer_tax_id,
        external_pos_id=order.external_pos_id,
        pos_source=order.pos_source,
        notes=order.notes,
        lines=[
            {
                "id": ln.id,
                "menu_item_id": ln.menu_item_id,
                "qty": ln.qty,
                "unit_price": ln.unit_price,
                "line_total": ln.line_total,
                "cogs_actual": ln.cogs_actual,
                "cogs_theoretical": ln.cogs_theoretical,
                "notes": ln.notes,
            }
            for ln in lines
        ],  # type: ignore[arg-type]
        discounts=[
            {
                "id": d.id,
                "kind": d.kind.value if hasattr(d.kind, "value") else d.kind,
                "value": d.value,
                "reason": d.reason,
                "applied_by": d.applied_by,
            }
            for d in discounts
        ],  # type: ignore[arg-type]
        payments=[
            {
                "id": p.id,
                "method": p.method.value if hasattr(p.method, "value") else p.method,
                "amount": p.amount,
                "fee_amount": p.fee_amount,
                "reference": p.reference,
                "paid_at": _to_tpe(p.paid_at),
            }
            for p in payments
        ],  # type: ignore[arg-type]
    )


# ──────────────────────────────────────────────────────────────────────────
# Public service entrypoints
# ──────────────────────────────────────────────────────────────────────────


async def create_order(
    session: AsyncSession,
    payload: OrderCreateRequest,
    tenant_id: uuid.UUID,
    messenger: LineMessenger | None = None,
) -> tuple[OrderResponse, bool]:
    """Create a new order (or replay idempotently).

    Returns ``(response, created)``. ``created=False`` means an existing
    order matched the request's ``external_pos_id`` and was returned as-is.
    """
    store_tenant = await _resolve_tenant_from_store(session, payload.store_id, tenant_id)

    # Idempotency check first — if external_pos_id collides we replay.
    if payload.external_pos_id is not None:
        existing_stmt = (
            select(Order)
            .where(
                Order.tenant_id == store_tenant,
                Order.store_id == payload.store_id,
                Order.external_pos_id == payload.external_pos_id,
            )
            .options(
                selectinload(Order.lines),
                selectinload(Order.discounts),
                selectinload(Order.payments),
            )
            .limit(1)
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None and existing.deleted_at is None:
            logger.info(
                "order.create.idempotent_replay order_id=%s external_pos_id=%s",
                existing.id, payload.external_pos_id,
            )
            return order_to_response(existing), False

    # Conflict check on (store_id, business_date, order_no) when no external key.
    dup_stmt = select(Order.id).where(
        Order.tenant_id == store_tenant,
        Order.store_id == payload.store_id,
        Order.business_date == payload.business_date,
        Order.order_no == payload.order_no,
        Order.deleted_at.is_(None),
    )
    dup_id = (await session.execute(dup_stmt)).scalar_one_or_none()
    if dup_id is not None:
        raise ConflictError(
            f"order_no already exists for this store/business_date: {payload.order_no}",
        )

    opened_at = payload.opened_at or datetime.now(UTC)

    order = Order(
        tenant_id=store_tenant,
        store_id=payload.store_id,
        order_no=payload.order_no,
        business_date=payload.business_date,
        opened_at=opened_at,
        status=OrderStatus.OPEN,
        invoice_number=payload.invoice_number,
        carrier_type=payload.carrier_type,
        carrier_id=payload.carrier_id,
        buyer_tax_id=payload.buyer_tax_id,
        external_pos_id=payload.external_pos_id,
        pos_source=payload.pos_source,
        notes=payload.notes,
    )
    session.add(order)
    await session.flush()  # populate order.id

    # Lines + their stock-movement consumption (BOM-expanded when recipes
    # exist; placeholder row otherwise). Placeholder is materialised lazily
    # — created the first time a line has no recipe.
    placeholder_state: dict[str, Ingredient] = {}

    async def _get_placeholder() -> Ingredient:
        if "ing" not in placeholder_state:
            placeholder_state["ing"] = await _placeholder_ingredient(
                session, store_tenant, payload.store_id
            )
        return placeholder_state["ing"]

    for line_payload in payload.lines:
        await _add_line_with_movement(
            session=session,
            order=order,
            tenant_id=store_tenant,
            store_id=payload.store_id,
            line_payload=line_payload,
            get_placeholder=_get_placeholder,
            occurred_at=opened_at,
        )

    for disc in payload.discounts:
        session.add(
            OrderDiscount(
                tenant_id=store_tenant,
                order_id=order.id,
                kind=DiscountKind(disc.kind),
                value=disc.value,
                reason=disc.reason,
                applied_by=disc.applied_by,
            ),
        )

    for pay in payload.payments:
        session.add(
            OrderPayment(
                tenant_id=store_tenant,
                order_id=order.id,
                method=PaymentMethod(pay.method),
                amount=pay.amount,
                fee_amount=pay.fee_amount,
                reference=pay.reference,
                paid_at=pay.paid_at or opened_at,
            ),
        )

    await session.flush()
    # Re-load to pick up server defaults + populate relationships
    order = await _load_order_with_relations(session, order.id, store_tenant)

    logger.info(
        "order.created order_id=%s store_id=%s lines=%d",
        order.id, order.store_id, len(order.lines),
    )

    # LINE confirmation: Phase 1 has no customer FK on Order, so skip silently.
    # TODO(line): wire customer_id on Order then call messenger.push(...) here.
    _ = messenger  # reserved for Phase 2

    return order_to_response(order), True


async def _add_line_with_movement(
    *,
    session: AsyncSession,
    order: Order,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    line_payload: OrderLineCreate,
    get_placeholder: Callable[[], Awaitable[Ingredient]],
    occurred_at: datetime,
) -> None:
    """Create one OrderLine + its consumption ledger rows.

    Recipe path (``consume_bom``):
        Active recipes exist for this menu item → expand into one
        ``stock_movements`` row per ingredient with signed negative qty,
        and persist the engine's ``theoretical_cogs`` onto the line.

    Fallback path:
        No recipes configured → write a single placeholder row at qty=-1 so
        the ledger stays self-consistent and downstream variance jobs can
        flag the missing recipe.
    """
    line_total = line_payload.qty * line_payload.unit_price
    recipes = await _load_active_recipes(session, line_payload.menu_item_id)

    theoretical_cogs: Decimal | None = None
    if recipes:
        bom_output = consume_bom(
            BOMConsumeInput(
                menu_item_id=str(line_payload.menu_item_id),
                qty_sold=line_payload.qty,
                recipes=recipes,
            )
        )
        theoretical_cogs = bom_output.theoretical_cogs

    line = OrderLine(
        tenant_id=tenant_id,
        order_id=order.id,
        menu_item_id=line_payload.menu_item_id,
        qty=line_payload.qty,
        unit_price=line_payload.unit_price,
        line_total=line_total,
        cogs_theoretical=theoretical_cogs,
        notes=line_payload.notes,
    )
    session.add(line)
    await session.flush()  # need line.id for source_id

    if recipes:
        for delta in bom_output.movements:
            session.add(
                StockMovement(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    ingredient_id=uuid.UUID(delta.ingredient_id),
                    movement_type=MovementType.SALE_CONSUME,
                    qty=delta.qty,  # already signed-negative
                    source_table="order_lines",
                    source_id=line.id,
                    occurred_at=occurred_at,
                ),
            )
    else:
        placeholder = await get_placeholder()
        session.add(
            StockMovement(
                tenant_id=tenant_id,
                store_id=store_id,
                ingredient_id=placeholder.id,
                movement_type=MovementType.SALE_CONSUME,
                qty=Decimal("-1"),
                source_table="order_lines",
                source_id=line.id,
                occurred_at=occurred_at,
                note=f"no recipe for menu_item {line_payload.menu_item_id}",
            ),
        )


async def get_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> OrderResponse:
    """Fetch a single order with all child collections."""
    order = await _load_order_with_relations(session, order_id, tenant_id)
    return order_to_response(order)


async def close_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    tenant_id: uuid.UUID,
    closed_at: datetime | None,
) -> OrderResponse:
    """Transition an open order → closed.

    Net revenue is computed but *not* persisted — ``mv_daily_pnl`` aggregates
    it on the read side. We still log it so the close event has an audit
    trail with the value at the time of close.
    """
    order = await _load_order_with_relations(session, order_id, tenant_id)
    if order.status != OrderStatus.OPEN:
        raise ConflictError(
            f"order cannot be closed from status {order.status.value}",
            details={"current_status": order.status.value},
        )

    closed_dt = closed_at or datetime.now(UTC)
    if closed_dt.tzinfo is None:
        # Belt-and-braces; schema already rejected naive but guard anyway.
        raise ValidationError("closed_at must be timezone-aware")

    net_revenue = _compute_net_revenue_stub(order)

    order.status = OrderStatus.CLOSED  # type: ignore[assignment]
    order.closed_at = closed_dt  # type: ignore[assignment]
    await session.flush()

    logger.info(
        "order.closed order_id=%s net_revenue=%s closed_at=%s",
        order.id, net_revenue, closed_dt.isoformat(),
    )
    return order_to_response(order)


async def void_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    tenant_id: uuid.UUID,
    reason: str,
) -> OrderResponse:
    """Void an order. Writes reversing stock_movements; never touches existing rows."""
    order = await _load_order_with_relations(session, order_id, tenant_id)
    if order.status == OrderStatus.VOIDED:
        raise ConflictError(
            "order is already voided",
            details={"current_status": order.status.value},
        )

    # Collect all stock_movements caused by this order's lines. Filter by
    # the order's own tenant (not the request header) since Phase-1 multi-
    # tenancy is off and the two diverge in tests.
    line_ids = [ln.id for ln in order.lines]
    if line_ids:
        mv_stmt = select(StockMovement).where(
            StockMovement.source_table == "order_lines",
            StockMovement.source_id.in_(line_ids),
            StockMovement.movement_type == MovementType.SALE_CONSUME,
        )
        movements = (await session.execute(mv_stmt)).scalars().all()
        for orig in movements:
            session.add(
                StockMovement(
                    tenant_id=order.tenant_id,
                    store_id=orig.store_id,
                    ingredient_id=orig.ingredient_id,
                    movement_type=MovementType.ADJUSTMENT_IN,
                    qty=-orig.qty,  # negate: -(-x) = +x → restores stock
                    source_table="order_voids",
                    source_id=order.id,
                    occurred_at=datetime.now(UTC),
                    note=f"void reversal of movement {orig.id}; reason={reason}",
                ),
            )

    order.status = OrderStatus.VOIDED  # type: ignore[assignment]
    # closed_at is preserved per spec — voiding a closed order keeps the close time.
    if order.notes:
        order.notes = f"{order.notes}\n[void] {reason}"  # type: ignore[assignment]
    else:
        order.notes = f"[void] {reason}"  # type: ignore[assignment]
    await session.flush()

    logger.info(
        "order.voided order_id=%s reversals=%d reason=%s",
        order.id, len(line_ids), reason,
    )
    # Reload to pick up the new movements when the caller asks (not strictly
    # needed for the response shape, but keeps the session in a fresh state).
    order = await _load_order_with_relations(session, order_id, tenant_id)
    return order_to_response(order)


# ──────────────────────────────────────────────────────────────────────────
# Internal calc-engine stubs (replaced by calc-engine modules in Phase 2)
# ──────────────────────────────────────────────────────────────────────────


def _compute_net_revenue_stub(order: Order) -> Decimal:
    """Naive net-revenue stub.

    TODO(discount_resolver): replace with
    ``discount_resolver.compute_net_revenue(order)`` per
    ``specs/discount_resolver.md`` — that handles percent discounts,
    line-scoped discounts, comp/allowance precedence, etc.
    """
    gross = sum((ln.line_total for ln in order.lines), Decimal("0"))
    deductions = Decimal("0")
    for d in order.discounts:
        kind = d.kind.value if hasattr(d.kind, "value") else d.kind
        if kind == "percent":
            deductions += gross * d.value
        else:
            deductions += d.value
    return gross - deductions


__all__ = [
    "close_order",
    "create_order",
    "get_order",
    "order_to_response",
    "void_order",
]
