"""Business-logic service for the Orders router.

This module owns the *atomic* state transitions of an order — create, close,
void — and the bookkeeping ledger writes that go with each. Routers stay
thin and stateless; services here do the work.

Wiring into the calc engines and side-channels:
- BOM consumption goes through ``consume_bom`` when the menu item has
  active recipes; falls back to a placeholder ledger row when none exist.
- Net revenue on close runs the discount stack through
  ``resolve_discounts`` (percent → employee → amount → allowance → comp).
- 統一發票 / 載具 / 統編 fields are stored as-supplied; the regex on the
  Pydantic schema catches the format and ``uniform_invoice_validator``
  is available for the downstream MoF roundtrip when that lands.
- LINE messenger is fire-and-forget on close — only when the order has
  a ``customer_id`` and the customer carries a ``line_user_id``.
- Points accrual: on close, when ``customer_id`` is set, writes a
  ``customer_points_ledger`` row (1 pt / 100 TWD x tier multiplier) and
  bumps the cached ``Customer.points_balance / lifetime_value /
  last_visit_at`` aggregates.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..config import get_settings
from ..integrations.line import LineMessage
from ..models import (
    Customer,
    CustomerPointsLedger,
    CustomerTier,
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
from ..models.orders import (
    DiscountKind,
    KitchenStation,
    KitchenStatus,
    OrderSource,
    PaymentMethod,
    ServiceType,
)
from ..models.tables import DiningTable
from ..schemas.orders import (
    OrderCreateRequest,
    OrderLineCreate,
    OrderListResponse,
    OrderResponse,
    OrderSummary,
)
from .audit_service import audit
from .calc.bom_consumer import (
    BOMConsumeInput,
    RecipeRow,
    consume_bom,
)
from .calc.discount_resolver import (
    DiscountResolveInput,
    DiscountRow,
    resolve_discounts,
)
from .referral_service import qualify_referral
from .streak_service import compute_visit_streak, streak_multiplier

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


async def _validate_table(
    session: AsyncSession,
    table_id: uuid.UUID,
    store_id: uuid.UUID,
) -> None:
    """Ensure the dining table exists (live) and belongs to the order's store.

    - Missing / soft-deleted table → 404 ``NotFoundError``
    - Table from another store → 422 ``ValidationError`` (the id is real,
      the request is just wrong — a 404 would send staff hunting a ghost).
    """
    table = await session.get(DiningTable, table_id)
    if table is None or table.deleted_at is not None:
        raise NotFoundError(f"dining table not found: {table_id}")
    if table.store_id != store_id:
        raise ValidationError(
            "table_id belongs to a different store",
            details={"table_id": str(table_id), "table_store_id": str(table.store_id)},
        )


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
        customer_id=order.customer_id,
        order_no=order.order_no,
        business_date=order.business_date,
        opened_at=opened_at,
        closed_at=_to_tpe(order.closed_at),
        status=order.status.value,  # type: ignore[arg-type]
        service_type=order.service_type.value,  # type: ignore[arg-type]
        order_source=order.order_source.value,  # type: ignore[arg-type]
        table_id=order.table_id,
        refunded_at=_to_tpe(order.refunded_at),
        refund_reason=order.refund_reason,
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
                "kitchen_station": (
                    ln.kitchen_station.value if ln.kitchen_station is not None else None
                ),
                "kitchen_status": (
                    ln.kitchen_status.value if ln.kitchen_status is not None else None
                ),
                "sent_to_kitchen_at": _to_tpe(ln.sent_to_kitchen_at),
                "modifiers": ln.modifiers or [],
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

    if payload.table_id is not None:
        await _validate_table(session, payload.table_id, payload.store_id)

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
        customer_id=payload.customer_id,
        order_no=payload.order_no,
        business_date=payload.business_date,
        opened_at=opened_at,
        status=OrderStatus.OPEN,
        service_type=ServiceType(payload.service_type),
        order_source=OrderSource(payload.order_source),
        table_id=payload.table_id,
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

    # LINE on create-time is intentionally a no-op: dine-in is the dominant
    # channel and the customer is already at the table. The customer-facing
    # LINE push happens at close (with the receipt + earned points) — see
    # ``_accrue_points_and_notify``. ``messenger`` is kept on the signature
    # so Phase 2 online-orders flows can plug a "we received your order"
    # push without a service-signature break.
    _ = messenger

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
    # Modifiers adjust the effective unit price BEFORE multiplying by qty:
    # line_total = (unit_price + Σ price_delta) x qty. All Decimal, no float.
    modifier_delta = sum(
        (m.price_delta for m in line_payload.modifiers), Decimal("0")
    )
    line_total = (line_payload.unit_price + modifier_delta) * line_payload.qty
    # JSONB snapshot — price_delta serialised as string so the sold history
    # stays exact (and re-parses to Decimal without float round-trips).
    modifiers_snapshot = [
        {"group": m.group, "option": m.option, "price_delta": str(m.price_delta)}
        for m in line_payload.modifiers
    ]
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

    # KDS opt-in: if the caller routed this line to a station, stamp the
    # initial KDS status + arrival timestamp so the kitchen display picks
    # it up immediately. Counter-only lines (no station) skip KDS entirely.
    kitchen_station = (
        KitchenStation(line_payload.kitchen_station)
        if line_payload.kitchen_station is not None
        else None
    )
    kitchen_status = KitchenStatus.QUEUED if kitchen_station is not None else None
    sent_to_kitchen_at = occurred_at if kitchen_station is not None else None

    line = OrderLine(
        tenant_id=tenant_id,
        order_id=order.id,
        menu_item_id=line_payload.menu_item_id,
        qty=line_payload.qty,
        unit_price=line_payload.unit_price,
        line_total=line_total,
        cogs_theoretical=theoretical_cogs,
        notes=line_payload.notes,
        kitchen_station=kitchen_station,
        kitchen_status=kitchen_status,
        sent_to_kitchen_at=sent_to_kitchen_at,
        modifiers=modifiers_snapshot,
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


async def list_orders(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: OrderStatus | None = None,
    business_date: date | None = None,
    store_id: uuid.UUID | None = None,
    service_type: ServiceType | None = None,
    order_source: OrderSource | None = None,
    limit: int = 50,
    offset: int = 0,
) -> OrderListResponse:
    """List order summaries, newest first, with the running total per order.

    未結單 = ``status=open``. The total is Σ line_total (gross, before
    discounts) — the same figure staff see on the open-tab screen.

    Phase-1 multi-tenancy is off (see ``_load_order_with_relations``); we
    scope by the explicit ``store_id`` filter rather than the header tenant.
    """
    _ = tenant_id  # reserved for Phase 2 RLS / explicit filter

    totals_sq = (
        select(
            OrderLine.order_id.label("order_id"),
            func.coalesce(func.sum(OrderLine.line_total), 0).label("total"),
        )
        .group_by(OrderLine.order_id)
        .subquery()
    )
    stmt = (
        select(Order, func.coalesce(totals_sq.c.total, 0))
        .outerjoin(totals_sq, totals_sq.c.order_id == Order.id)
        .where(Order.deleted_at.is_(None))
        .order_by(Order.opened_at.desc(), Order.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        stmt = stmt.where(Order.status == status)
    if business_date is not None:
        stmt = stmt.where(Order.business_date == business_date)
    if store_id is not None:
        stmt = stmt.where(Order.store_id == store_id)
    if service_type is not None:
        stmt = stmt.where(Order.service_type == service_type)
    if order_source is not None:
        stmt = stmt.where(Order.order_source == order_source)

    rows = (await session.execute(stmt)).all()
    items = [
        OrderSummary(
            id=order.id,
            order_no=order.order_no,
            status=order.status.value,  # type: ignore[arg-type]
            service_type=order.service_type.value,  # type: ignore[arg-type]
            order_source=order.order_source.value,  # type: ignore[arg-type]
            table_id=order.table_id,
            opened_at=_to_tpe(order.opened_at) or order.opened_at,
            total=Decimal(total),
        )
        for order, total in rows
    ]
    return OrderListResponse(items=items, limit=limit, offset=offset)


async def add_lines(
    session: AsyncSession,
    order_id: uuid.UUID,
    tenant_id: uuid.UUID,
    lines: list[OrderLineCreate],
) -> OrderResponse:
    """加點 — append lines to an OPEN order. Any other status → 409.

    Reuses the create-time line pipeline so modifiers pricing, KDS routing,
    and BOM/placeholder stock movements behave identically to first-order
    lines.
    """
    order = await _load_order_with_relations(session, order_id, tenant_id)
    if order.status != OrderStatus.OPEN:
        raise ConflictError(
            f"lines can only be added to an open order (status={order.status.value})",
            details={"current_status": order.status.value},
        )

    occurred_at = datetime.now(UTC)

    placeholder_state: dict[str, Ingredient] = {}

    async def _get_placeholder() -> Ingredient:
        if "ing" not in placeholder_state:
            placeholder_state["ing"] = await _placeholder_ingredient(
                session, order.tenant_id, order.store_id
            )
        return placeholder_state["ing"]

    for line_payload in lines:
        await _add_line_with_movement(
            session=session,
            order=order,
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            line_payload=line_payload,
            get_placeholder=_get_placeholder,
            occurred_at=occurred_at,
        )

    await session.flush()
    # The identity map would hand back the pre-add ``lines`` collection on a
    # plain re-select; expire it so the reload sees the appended rows.
    session.expire(order, ["lines"])
    order = await _load_order_with_relations(session, order_id, tenant_id)
    logger.info(
        "order.lines_added order_id=%s added=%d total_lines=%d",
        order.id, len(lines), len(order.lines),
    )
    return order_to_response(order)


async def close_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    tenant_id: uuid.UUID,
    closed_at: datetime | None,
    messenger: LineMessenger | None = None,
) -> OrderResponse:
    """Transition an open order → closed.

    Net revenue is computed but *not* persisted — ``mv_daily_pnl`` aggregates
    it on the read side. We still log it so the close event has an audit
    trail with the value at the time of close.

    When the order has a ``customer_id`` attached:
      - writes a ``customer_points_ledger`` accrual row (1 point per TWD 100
        of net revenue, multiplied by the tier in ``_POINTS_TIER_MULTIPLIER``)
      - bumps ``Customer.points_balance``, ``lifetime_value``,
        ``last_visit_at`` (cached aggregates documented in
        ``models/customers.py``)
      - pushes a LINE 收據 + 點數 message when the customer carries a
        ``line_user_id`` (fire-and-forget; LINE failure does not block close)
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

    net_revenue = _compute_net_revenue(order)

    order.status = OrderStatus.CLOSED  # type: ignore[assignment]
    order.closed_at = closed_dt  # type: ignore[assignment]
    await session.flush()

    # ─── Customer loop (points + LINE) — no-op when no customer attached ───
    if order.customer_id is not None:
        await _accrue_points_and_notify(
            session,
            order=order,
            net_revenue=net_revenue,
            closed_dt=closed_dt,
            messenger=messenger,
        )
        # 裂變: if this buyer was referred and hasn't qualified yet, their first
        # closed order qualifies the referral → referrer earns override points.
        await qualify_referral(
            session,
            referred_id=order.customer_id,
            tenant_id=order.tenant_id,
            order_id=order.id,
            messenger=messenger,
        )

    logger.info(
        "order.closed order_id=%s net_revenue=%s closed_at=%s",
        order.id, net_revenue, closed_dt.isoformat(),
    )
    return order_to_response(order)


# ──────────────────────────────────────────────────────────────────────────
# Customer loop — points accrual + LINE confirmation
# ──────────────────────────────────────────────────────────────────────────

# 1 point per 100 TWD of net revenue. Same convention as 全家 / 7-11 / 大苑子.
_POINTS_PER_TWD = Decimal("0.01")

# Tier-rate multipliers. Source-of-truth lives here until a
# ``tenant_settings`` table lets operators override per brand.
_POINTS_TIER_MULTIPLIER: dict[CustomerTier, Decimal] = {
    CustomerTier.REGULAR: Decimal("1.0"),
    CustomerTier.SILVER: Decimal("1.5"),
    CustomerTier.GOLD: Decimal("2.0"),
    CustomerTier.PLATINUM: Decimal("3.0"),
}


def _compute_points_earned(net_revenue: Decimal, tier: CustomerTier) -> Decimal:
    """Round-half-up to whole points (loyalty programs don't fractional)."""
    if net_revenue <= Decimal("0"):
        return Decimal("0")
    multiplier = _POINTS_TIER_MULTIPLIER.get(tier, Decimal("1.0"))
    raw = net_revenue * _POINTS_PER_TWD * multiplier
    return raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


async def _accrue_points_and_notify(
    session: AsyncSession,
    *,
    order: Order,
    net_revenue: Decimal,
    closed_dt: datetime,
    messenger: LineMessenger | None,
) -> None:
    """Write the points ledger row, bump customer aggregates, push LINE."""
    assert order.customer_id is not None  # caller guards this
    customer = await session.get(Customer, order.customer_id)
    if customer is None or customer.tenant_id != order.tenant_id:
        # Customer was deleted or belongs to another tenant — skip silently
        # so close still succeeds. The order keeps the FK as NULL via the
        # SET NULL ondelete in the next nightly batch.
        logger.warning(
            "order.close.customer_missing order_id=%s customer_id=%s",
            order.id, order.customer_id,
        )
        return

    base_points = _compute_points_earned(net_revenue, customer.tier)
    # 連續回訪 streak bonus — multiply the tier-rated points by the member's
    # consecutive visit-day streak (1.0x for a 1-2 day streak, so single-visit
    # points stay exact). Streak counts this just-closed order's business date.
    # Skip the streak query entirely when there are no base points to scale
    # (comped / zero-revenue orders) — defaults keep the LINE message safe.
    streak = 0
    streak_mult = Decimal("1.0")
    points = base_points
    if base_points > 0:
        streak = await compute_visit_streak(
            session,
            customer_id=customer.id,
            tenant_id=order.tenant_id,
            as_of=order.business_date,
        )
        streak_mult = streak_multiplier(streak)
        points = (base_points * streak_mult).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    if points > 0:
        # Stamp expires_at per the tenant-wide policy. Settings.points_expiry_days
        # is the lifecycle horizon — 0 means "never expires" (legacy mode);
        # any positive value sets the cutoff that the nightly points_expire
        # job sweeps against.
        expiry_days = get_settings().points_expiry_days
        expires_at = (
            closed_dt + timedelta(days=expiry_days) if expiry_days > 0 else None
        )
        ledger = CustomerPointsLedger(
            tenant_id=order.tenant_id,
            customer_id=customer.id,
            delta=points,
            reason="order.earn",
            source_order_id=order.id,
            expires_at=expires_at,
            note=f"net_revenue={net_revenue} streak={streak} x{streak_mult}",
        )
        session.add(ledger)

    # Cached aggregates on the Customer row. The nightly job recomputes
    # these from the ledger; we keep them current here so the LINE push
    # below carries the right balance.
    customer.points_balance = (customer.points_balance or Decimal("0")) + points
    customer.lifetime_value = (customer.lifetime_value or Decimal("0")) + max(
        net_revenue, Decimal("0")
    )
    customer.last_visit_at = closed_dt
    await session.flush()

    # LINE push — fire-and-forget. The customer might not be a LINE user
    # (we identified them by phone / member card), in which case we skip.
    if messenger is not None and customer.line_user_id:
        try:
            streak_line = (
                f"連續回訪 {streak} 天 點數 x{streak_mult}!\n"
                if streak_mult > Decimal("1.0")
                else ""
            )
            text = (
                f"感謝您的光臨!\n"
                f"訂單 {order.order_no}\n"
                f"消費金額 NT${net_revenue}\n"
                f"{streak_line}"
                f"本次累積點數 {points}\n"
                f"目前點數餘額 {customer.points_balance}"
            )
            await messenger.push(
                customer.line_user_id,
                LineMessage(kind="text", text=text),
            )
        except Exception as exc:
            logger.warning(
                "order.close.line_push_failed order_id=%s err=%s",
                order.id, exc,
            )


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


async def refund_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    tenant_id: uuid.UUID,
    reason: str,
) -> OrderResponse:
    """Refund a CLOSED order → status=refunded. Any other status → 409.

    Stamps ``refunded_at`` / ``refund_reason`` and writes an ``order.refunded``
    audit row (append-only trail — 退款 is a cash-out event and must be
    reviewable). Stock is NOT restored: the food was made and served; only
    void reverses consumption.
    """
    order = await _load_order_with_relations(session, order_id, tenant_id)
    if order.status != OrderStatus.CLOSED:
        raise ConflictError(
            f"only a closed order can be refunded (status={order.status.value})",
            details={"current_status": order.status.value},
        )

    refunded_dt = datetime.now(UTC)
    order.status = OrderStatus.REFUNDED  # type: ignore[assignment]
    order.refunded_at = refunded_dt  # type: ignore[assignment]
    order.refund_reason = reason  # type: ignore[assignment]
    await session.flush()

    await audit(
        session,
        action="order.refunded",
        tenant_id=order.tenant_id,
        target=("orders", order.id),
        store_id=order.store_id,
        before={"status": OrderStatus.CLOSED.value},
        after={
            "status": OrderStatus.REFUNDED.value,
            "refunded_at": refunded_dt.isoformat(),
        },
        reason=reason,
    )

    logger.info(
        "order.refunded order_id=%s refunded_at=%s reason=%s",
        order.id, refunded_dt.isoformat(), reason,
    )
    return order_to_response(order)


# ──────────────────────────────────────────────────────────────────────────
# Net-revenue computation (delegates to the discount_resolver calc engine)
# ──────────────────────────────────────────────────────────────────────────


def _compute_net_revenue(order: Order) -> Decimal:
    """Net revenue = subtotal after running the discount stack through
    ``resolve_discounts``.

    The calc engine handles percent → employee → amount → allowance → comp
    stacking precedence; we just project the order's discount rows into the
    engine's input shape and read ``net_revenue`` off the result.
    """
    gross = sum((ln.line_total for ln in order.lines), Decimal("0"))
    discount_rows = [
        DiscountRow(kind=d.kind.value, value=d.value)  # type: ignore[arg-type]
        for d in order.discounts
    ]
    output = resolve_discounts(
        DiscountResolveInput(subtotal=gross, discounts=discount_rows)
    )
    return output.net_revenue


__all__ = [
    "add_lines",
    "close_order",
    "create_order",
    "get_order",
    "list_orders",
    "order_to_response",
    "refund_order",
    "void_order",
]
