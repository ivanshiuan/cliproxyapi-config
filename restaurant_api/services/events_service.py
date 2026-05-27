"""Business logic for ``/events`` — waste / staff_meal / tasting.

Every endpoint writes a typed-row event AND a (or N) ``stock_movements`` row
inside the **same** async transaction. The ledger is the single source of
truth for "how much ingredient is left"; the typed row is the source of
truth for the cost line on ``mv_daily_pnl``. Both must agree, hence one txn.

Immutability
------------
This module never issues UPDATE/DELETE against ``waste_events`` /
``staff_meal_events`` / ``tasting_events``. Mistakes are fixed by appending
a reversal ``stock_movements`` row (out of scope for this router — see
``adjustments`` flow).

Idempotency (Phase 1)
---------------------
The event tables don't carry an ``external_ref`` column yet (intentional —
adds noise to the read shape and the column would be NULL for 99% of rows).
Instead, when a request supplies ``external_ref`` we:

1. Tag every ``stock_movements`` row we write for this event with
   ``note = "ext:<value>"``.
2. Before inserting, look for an existing movement with the same
   ``(store_id, source_table, note)`` triple.
3. If found, return the existing event (200) without writing again.

This keeps the dedup self-contained in the append-only ledger, which is
already the canonical record. When the calc-engine refactor lands a real
idempotency table, this swaps cleanly.

TODO: swap ``_expand_recipe_stub`` for the real ``bom_consumer`` once the
calc-engine module exposes it. The current implementation reads from the
``recipes`` table directly (current version: ``effective_to IS NULL``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import NotFoundError, ValidationError
from ..models import (
    Employee,
    Ingredient,
    MenuItem,
    MovementType,
    Recipe,
    StaffMealEvent,
    StockMovement,
    Store,
    TastingEvent,
    WasteEvent,
)
from ..schemas.events import (
    EventListItem,
    EventTypeLiteral,
    StaffMealEventCreate,
    StaffMealEventResponse,
    TastingEventCreate,
    TastingEventResponse,
    WasteEventCreate,
    WasteEventResponse,
)

TPE = ZoneInfo("Asia/Taipei")

_EXT_REF_PREFIX = "ext:"


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _to_tpe(dt: datetime) -> datetime:
    """Convert a (UTC or aware) datetime to Asia/Taipei for response shaping."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TPE)


def _ext_note(external_ref: str | None) -> str | None:
    return f"{_EXT_REF_PREFIX}{external_ref}" if external_ref else None


async def _require_store(session: AsyncSession, store_id: uuid.UUID) -> Store:
    res = await session.get(Store, store_id)
    if res is None:
        raise NotFoundError("store not found", details={"store_id": str(store_id)})
    return res


async def _require_ingredient(session: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient:
    res = await session.get(Ingredient, ingredient_id)
    if res is None or not res.is_active:
        raise NotFoundError(
            "ingredient not found", details={"ingredient_id": str(ingredient_id)}
        )
    return res


async def _require_employee(session: AsyncSession, employee_id: uuid.UUID) -> Employee:
    res = await session.get(Employee, employee_id)
    if res is None:
        raise NotFoundError(
            "employee not found", details={"employee_id": str(employee_id)}
        )
    return res


async def _require_menu_item(session: AsyncSession, menu_item_id: uuid.UUID) -> MenuItem:
    res = await session.get(MenuItem, menu_item_id)
    if res is None:
        raise NotFoundError(
            "menu item not found", details={"menu_item_id": str(menu_item_id)}
        )
    return res


async def _expand_recipe_stub(
    session: AsyncSession, menu_item_id: uuid.UUID
) -> list[tuple[uuid.UUID, Decimal]]:
    """Return [(ingredient_id, qty_per_serving)] for the current BOM.

    Phase 1 stub: reads ``recipes`` directly (``effective_to IS NULL``).
    TODO: replace with ``calc_engine.bom_consumer.expand(menu_item_id)`` when
    that module exposes the helper.
    """
    stmt = select(Recipe.ingredient_id, Recipe.qty_per_serving).where(
        Recipe.menu_item_id == menu_item_id,
        Recipe.effective_to.is_(None),
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


async def _find_idempotent_event(
    session: AsyncSession,
    *,
    store_id: uuid.UUID,
    source_table: str,
    external_ref: str,
) -> uuid.UUID | None:
    """Return the ``source_id`` of an existing event tagged with ``external_ref``."""
    stmt = (
        select(StockMovement.source_id)
        .where(
            StockMovement.store_id == store_id,
            StockMovement.source_table == source_table,
            StockMovement.note == _ext_note(external_ref),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _resolve_tenant_id(store: Store) -> uuid.UUID:
    """MVP: tenant is derived from the store row (no auth context yet)."""
    return store.tenant_id


# ──────────────────────────────────────────────────────────────────────────
# Waste
# ──────────────────────────────────────────────────────────────────────────


async def create_waste_event(
    session: AsyncSession,
    payload: WasteEventCreate,
) -> tuple[WasteEventResponse, bool]:
    """Create a waste event + matching stock_movements row.

    Returns ``(response, created)``. ``created=False`` means an idempotent
    replay hit — caller should return HTTP 200; ``True`` is HTTP 201.
    """
    store = await _require_store(session, payload.store_id)
    await _require_ingredient(session, payload.ingredient_id)
    if payload.reported_by is not None:
        await _require_employee(session, payload.reported_by)

    if payload.external_ref:
        existing_id = await _find_idempotent_event(
            session,
            store_id=payload.store_id,
            source_table="waste_events",
            external_ref=payload.external_ref,
        )
        if existing_id is not None:
            row = await session.get(WasteEvent, existing_id)
            mov_id = await _existing_movement_id(session, "waste_events", existing_id)
            if row is not None:
                return _waste_response(row, mov_id), False

    occurred_at = payload.occurred_at or datetime.now(UTC)
    tenant_id = _resolve_tenant_id(store)

    event = WasteEvent(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        ingredient_id=payload.ingredient_id,
        qty=payload.qty,
        cost=payload.cost,
        reason=payload.reason,
        reported_by=payload.reported_by,
        occurred_at=occurred_at,
    )
    session.add(event)
    await session.flush()

    movement = StockMovement(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        ingredient_id=payload.ingredient_id,
        movement_type=MovementType.WASTE,
        qty=-payload.qty,
        source_table="waste_events",
        source_id=event.id,
        occurred_at=occurred_at,
        note=_ext_note(payload.external_ref),
    )
    session.add(movement)
    await session.flush()

    return _waste_response(event, movement.id), True


def _waste_response(event: WasteEvent, movement_id: uuid.UUID | None) -> WasteEventResponse:
    return WasteEventResponse(
        id=event.id,
        store_id=event.store_id,
        ingredient_id=event.ingredient_id,
        qty=event.qty,
        cost=event.cost,
        reason=event.reason,
        reported_by=event.reported_by,
        occurred_at=_to_tpe(event.occurred_at),
        movement_id=movement_id,
    )


# ──────────────────────────────────────────────────────────────────────────
# Staff meal
# ──────────────────────────────────────────────────────────────────────────


async def create_staff_meal_event(
    session: AsyncSession,
    payload: StaffMealEventCreate,
) -> tuple[StaffMealEventResponse, bool]:
    store = await _require_store(session, payload.store_id)
    await _require_employee(session, payload.employee_id)

    lines: list[tuple[uuid.UUID, Decimal]]
    if payload.menu_item_id is not None:
        await _require_menu_item(session, payload.menu_item_id)
        lines = await _expand_recipe_stub(session, payload.menu_item_id)
        if not lines:
            raise ValidationError(
                "menu item has no recipe",
                details={"code": "no_recipe", "menu_item_id": str(payload.menu_item_id)},
            )
    else:
        assert payload.ingredients_used is not None
        # Validate referenced ingredients exist.
        for ing_id in payload.ingredients_used:
            await _require_ingredient(session, ing_id)
        lines = list(payload.ingredients_used.items())

    if payload.external_ref:
        existing_id = await _find_idempotent_event(
            session,
            store_id=payload.store_id,
            source_table="staff_meal_events",
            external_ref=payload.external_ref,
        )
        if existing_id is not None:
            row = await session.get(StaffMealEvent, existing_id)
            if row is not None:
                count = await _count_movements(session, "staff_meal_events", existing_id)
                return _staff_meal_response(row, count), False

    occurred_at = payload.occurred_at or datetime.now(UTC)
    tenant_id = _resolve_tenant_id(store)

    event = StaffMealEvent(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        employee_id=payload.employee_id,
        menu_item_id=payload.menu_item_id,
        ingredients_used=(
            {str(k): str(v) for k, v in payload.ingredients_used.items()}
            if payload.ingredients_used is not None
            else None
        ),
        total_cost=payload.total_cost,
        occurred_at=occurred_at,
    )
    session.add(event)
    await session.flush()

    for ing_id, qty in lines:
        session.add(
            StockMovement(
                tenant_id=tenant_id,
                store_id=payload.store_id,
                ingredient_id=ing_id,
                movement_type=MovementType.STAFF_MEAL,
                qty=-qty,
                source_table="staff_meal_events",
                source_id=event.id,
                occurred_at=occurred_at,
                note=_ext_note(payload.external_ref),
            )
        )
    await session.flush()

    return _staff_meal_response(event, len(lines)), True


def _staff_meal_response(
    event: StaffMealEvent, movements_created: int
) -> StaffMealEventResponse:
    used = event.ingredients_used
    typed_used: dict[uuid.UUID, Decimal] | None = None
    if used is not None:
        typed_used = {uuid.UUID(k): Decimal(str(v)) for k, v in used.items()}
    return StaffMealEventResponse(
        id=event.id,
        store_id=event.store_id,
        employee_id=event.employee_id,
        menu_item_id=event.menu_item_id,
        ingredients_used=typed_used,
        total_cost=event.total_cost,
        occurred_at=_to_tpe(event.occurred_at),
        movements_created=movements_created,
    )


# ──────────────────────────────────────────────────────────────────────────
# Tasting
# ──────────────────────────────────────────────────────────────────────────


async def create_tasting_event(
    session: AsyncSession,
    payload: TastingEventCreate,
) -> tuple[TastingEventResponse, bool]:
    store = await _require_store(session, payload.store_id)

    lines: list[tuple[uuid.UUID, Decimal]]
    if payload.menu_item_id is not None:
        await _require_menu_item(session, payload.menu_item_id)
        lines = await _expand_recipe_stub(session, payload.menu_item_id)
        if not lines:
            raise ValidationError(
                "menu item has no recipe",
                details={"code": "no_recipe", "menu_item_id": str(payload.menu_item_id)},
            )
    else:
        assert payload.ingredient_id is not None
        await _require_ingredient(session, payload.ingredient_id)
        lines = [(payload.ingredient_id, payload.qty)]

    if payload.external_ref:
        existing_id = await _find_idempotent_event(
            session,
            store_id=payload.store_id,
            source_table="tasting_events",
            external_ref=payload.external_ref,
        )
        if existing_id is not None:
            row = await session.get(TastingEvent, existing_id)
            if row is not None:
                count = await _count_movements(session, "tasting_events", existing_id)
                return _tasting_response(row, payload.ingredient_id, payload.qty, count), False

    occurred_at = payload.occurred_at or datetime.now(UTC)
    tenant_id = _resolve_tenant_id(store)

    event = TastingEvent(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        menu_item_id=payload.menu_item_id,
        purpose=payload.purpose,
        total_cost=payload.total_cost,
        occurred_at=occurred_at,
    )
    session.add(event)
    await session.flush()

    for ing_id, qty in lines:
        session.add(
            StockMovement(
                tenant_id=tenant_id,
                store_id=payload.store_id,
                ingredient_id=ing_id,
                movement_type=MovementType.TASTING,
                qty=-qty,
                source_table="tasting_events",
                source_id=event.id,
                occurred_at=occurred_at,
                note=_ext_note(payload.external_ref),
            )
        )
    await session.flush()

    return (
        _tasting_response(event, payload.ingredient_id, payload.qty, len(lines)),
        True,
    )


def _tasting_response(
    event: TastingEvent,
    ingredient_id: uuid.UUID | None,
    qty: Decimal,
    movements_created: int,
) -> TastingEventResponse:
    return TastingEventResponse(
        id=event.id,
        store_id=event.store_id,
        menu_item_id=event.menu_item_id,
        ingredient_id=ingredient_id,
        qty=qty,
        total_cost=event.total_cost,
        purpose=event.purpose,
        occurred_at=_to_tpe(event.occurred_at),
        movements_created=movements_created,
    )


# ──────────────────────────────────────────────────────────────────────────
# Movement bookkeeping helpers
# ──────────────────────────────────────────────────────────────────────────


async def _existing_movement_id(
    session: AsyncSession, source_table: str, source_id: uuid.UUID
) -> uuid.UUID | None:
    stmt = (
        select(StockMovement.id)
        .where(
            StockMovement.source_table == source_table,
            StockMovement.source_id == source_id,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _count_movements(
    session: AsyncSession, source_table: str, source_id: uuid.UUID
) -> int:
    from sqlalchemy import func

    stmt = select(func.count(StockMovement.id)).where(
        StockMovement.source_table == source_table,
        StockMovement.source_id == source_id,
    )
    return int((await session.execute(stmt)).scalar_one())


# ──────────────────────────────────────────────────────────────────────────
# List
# ──────────────────────────────────────────────────────────────────────────


def _tpe_day_to_utc_range(
    from_date: date | None, to_date: date | None
) -> tuple[datetime | None, datetime | None]:
    """Convert inclusive TPE day bounds to a UTC half-open window."""
    lo: datetime | None = None
    hi: datetime | None = None
    if from_date is not None:
        lo = datetime(from_date.year, from_date.month, from_date.day, tzinfo=TPE).astimezone(UTC)
    if to_date is not None:
        # End-of-day TPE → next-day 00:00 TPE in UTC for an exclusive upper bound.
        end_tpe = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, 999_999, tzinfo=TPE)
        hi = end_tpe.astimezone(UTC)
    return lo, hi


async def list_events(
    session: AsyncSession,
    *,
    store_id: uuid.UUID | None = None,
    event_type: EventTypeLiteral | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 200,
) -> list[EventListItem]:
    """Read across the three event tables and return a unified, sorted list."""
    if from_date is not None and to_date is not None and to_date < from_date:
        raise ValidationError(
            "to_date must be >= from_date",
            details={"from_date": str(from_date), "to_date": str(to_date)},
        )

    lo, hi = _tpe_day_to_utc_range(from_date, to_date)

    items: list[EventListItem] = []

    types_to_query: Sequence[Literal["waste", "staff_meal", "tasting"]] = (
        ("waste", "staff_meal", "tasting") if event_type is None else (event_type,)
    )

    for kind in types_to_query:
        if kind == "waste":
            stmt = select(WasteEvent)
            if store_id is not None:
                stmt = stmt.where(WasteEvent.store_id == store_id)
            if lo is not None:
                stmt = stmt.where(WasteEvent.occurred_at >= lo)
            if hi is not None:
                stmt = stmt.where(WasteEvent.occurred_at <= hi)
            stmt = stmt.order_by(WasteEvent.occurred_at.desc()).limit(limit)
            rows_waste = (await session.execute(stmt)).scalars().all()
            for r in rows_waste:
                mov_id = await _existing_movement_id(session, "waste_events", r.id)
                items.append(_waste_response(r, mov_id))
        elif kind == "staff_meal":
            stmt2 = select(StaffMealEvent)
            if store_id is not None:
                stmt2 = stmt2.where(StaffMealEvent.store_id == store_id)
            if lo is not None:
                stmt2 = stmt2.where(StaffMealEvent.occurred_at >= lo)
            if hi is not None:
                stmt2 = stmt2.where(StaffMealEvent.occurred_at <= hi)
            stmt2 = stmt2.order_by(StaffMealEvent.occurred_at.desc()).limit(limit)
            rows_sm = (await session.execute(stmt2)).scalars().all()
            for r2 in rows_sm:
                count = await _count_movements(session, "staff_meal_events", r2.id)
                items.append(_staff_meal_response(r2, count))
        else:  # tasting
            stmt3 = select(TastingEvent)
            if store_id is not None:
                stmt3 = stmt3.where(TastingEvent.store_id == store_id)
            if lo is not None:
                stmt3 = stmt3.where(TastingEvent.occurred_at >= lo)
            if hi is not None:
                stmt3 = stmt3.where(TastingEvent.occurred_at <= hi)
            stmt3 = stmt3.order_by(TastingEvent.occurred_at.desc()).limit(limit)
            rows_t = (await session.execute(stmt3)).scalars().all()
            for r3 in rows_t:
                count = await _count_movements(session, "tasting_events", r3.id)
                # ingredient_id / qty are derived: pull the first movement's row.
                mov_stmt = (
                    select(StockMovement.ingredient_id, StockMovement.qty)
                    .where(
                        StockMovement.source_table == "tasting_events",
                        StockMovement.source_id == r3.id,
                    )
                    .limit(1)
                )
                mov_row = (await session.execute(mov_stmt)).first()
                ing_id: uuid.UUID | None = None
                qty_val: Decimal = Decimal("0")
                if mov_row is not None:
                    ing_id = mov_row[0] if r3.menu_item_id is None else None
                    qty_val = -mov_row[1] if count == 1 else Decimal("0")
                items.append(_tasting_response(r3, ing_id, qty_val, count))

    items.sort(key=lambda e: e.occurred_at, reverse=True)
    return items[:limit]


__all__ = [
    "create_staff_meal_event",
    "create_tasting_event",
    "create_waste_event",
    "list_events",
]
