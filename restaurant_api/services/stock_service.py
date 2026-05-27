"""Stock ledger service — the write & read primitives behind ``/stock``.

This module is the **only** place we touch ``stock_movements`` for the
purchase / adjustment workflows. Two invariants are enforced here so the
router can stay thin:

1.  **Append-only.** No code path in this file calls ``UPDATE`` or ``DELETE``
    on ``stock_movements``. Idempotency works by *querying first* and skipping
    the INSERT — never by upsert. (The DB also blocks UPDATE/DELETE via
    ``INSTEAD NOTHING`` rules; this is belt-and-braces.)
2.  **Sign-by-type.** Inbound types (``purchase``, ``adjustment_in``,
    ``transfer_in``) MUST have ``qty > 0``; outbound types MUST have
    ``qty < 0``. We check this before the INSERT to surface a clean 422
    instead of letting the DB CHECK throw a bare ``IntegrityError``.

Forward compat: until the ``purchase_orders`` ORM model lands, supplier
metadata + per-line ``unit_cost`` live JSON-encoded in ``stock_movements.note``.
``_encode_purchase_note`` / ``_decode_purchase_note`` are the canonical
codec — when the real PO table arrives, rewrite this module's purchase
path and migrate; the wire-level schemas stay the same.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models import Ingredient, MovementType, StockMovement, Store
from ..models.base import uuid7
from ..schemas.stock import (
    AdjustmentCreateRequest,
    AdjustmentResponse,
    PurchaseCreateRequest,
    PurchaseLineResponse,
    PurchaseResponse,
    StockMovementResponse,
)

# Sign-by-type table — single source of truth used by the guard.
_INBOUND_TYPES: frozenset[MovementType] = frozenset(
    {MovementType.PURCHASE, MovementType.ADJUSTMENT_IN, MovementType.TRANSFER_IN}
)
_OUTBOUND_TYPES: frozenset[MovementType] = frozenset(
    {
        MovementType.SALE_CONSUME,
        MovementType.ADJUSTMENT_OUT,
        MovementType.WASTE,
        MovementType.STAFF_MEAL,
        MovementType.TASTING,
        MovementType.EXPIRY,
        MovementType.TRANSFER_OUT,
    }
)


# ──────────────────────────────────────────────────────────────────────────
# Note codec (stop-gap until purchase_orders ORM lands)
# ──────────────────────────────────────────────────────────────────────────


def _encode_purchase_note(
    *,
    supplier_name: str,
    supplier_tax_id: str | None,
    invoice_number: str,
    invoice_date: date,
    unit_cost: Decimal,
    expires_on: date | None,
    line_note: str | None,
    invoice_notes: str | None,
    external_ref: str | None,
) -> str:
    """Encode purchase metadata into a deterministic JSON string.

    Stored on ``stock_movements.note`` for every purchase row. Read back by
    ``_decode_purchase_note`` when projecting a purchase from the ledger.
    """
    payload: dict[str, Any] = {
        "kind": "purchase",
        "supplier_name": supplier_name,
        "supplier_tax_id": supplier_tax_id,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date.isoformat(),
        "unit_cost": str(unit_cost),
        "expires_on": expires_on.isoformat() if expires_on else None,
        "line_note": line_note,
        "invoice_notes": invoice_notes,
        "external_ref": external_ref,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _decode_purchase_note(raw: str | None) -> dict[str, Any]:
    """Best-effort decode; returns ``{}`` if not our JSON payload."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("kind") != "purchase":
        return {}
    return data


# ──────────────────────────────────────────────────────────────────────────
# Guards
# ──────────────────────────────────────────────────────────────────────────


def _assert_sign(movement_type: MovementType, qty: Decimal) -> None:
    """Raise 422 ``sign_violation`` before the DB CHECK can fire."""
    if movement_type in _INBOUND_TYPES and qty <= 0:
        raise ValidationError(
            "qty sign does not match movement_type",
            details={"code": "sign_violation", "movement_type": movement_type.value},
        )
    if movement_type in _OUTBOUND_TYPES and qty >= 0:
        raise ValidationError(
            "qty sign does not match movement_type",
            details={"code": "sign_violation", "movement_type": movement_type.value},
        )


async def _resolve_store(
    session: AsyncSession, tenant_id: uuid.UUID, store_id: uuid.UUID
) -> Store:
    """Look up a store scoped to the current tenant; 404 if absent."""
    stmt = select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id)
    store = (await session.execute(stmt)).scalar_one_or_none()
    if store is None:
        raise NotFoundError(
            "store not found", details={"store_id": str(store_id)}
        )
    return store


async def _resolve_ingredient(
    session: AsyncSession, tenant_id: uuid.UUID, ingredient_id: uuid.UUID
) -> Ingredient:
    """Look up an ingredient scoped to the current tenant; 404 if absent."""
    stmt = select(Ingredient).where(
        Ingredient.id == ingredient_id, Ingredient.tenant_id == tenant_id
    )
    ing = (await session.execute(stmt)).scalar_one_or_none()
    if ing is None:
        raise NotFoundError(
            "ingredient not found", details={"ingredient_id": str(ingredient_id)}
        )
    return ing


# ──────────────────────────────────────────────────────────────────────────
# Purchase
# ──────────────────────────────────────────────────────────────────────────


async def _find_existing_purchase_by_external_ref(
    session: AsyncSession, tenant_id: uuid.UUID, external_ref: str
) -> uuid.UUID | None:
    """Locate the synthetic ``purchase_invoice_id`` matching this external_ref."""
    stmt = select(StockMovement).where(
        StockMovement.tenant_id == tenant_id,
        StockMovement.movement_type == MovementType.PURCHASE,
        StockMovement.source_table == "purchase_invoices",
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        meta = _decode_purchase_note(row.note)
        if meta.get("external_ref") == external_ref and row.source_id is not None:
            return row.source_id
    return None


async def _find_existing_invoice_conflict(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    supplier_tax_id: str | None,
    invoice_number: str,
) -> bool:
    """Detect a duplicate ``(supplier_tax_id, invoice_number)`` already in ledger."""
    if supplier_tax_id is None:
        return False
    stmt = select(StockMovement).where(
        StockMovement.tenant_id == tenant_id,
        StockMovement.movement_type == MovementType.PURCHASE,
        StockMovement.source_table == "purchase_invoices",
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        meta = _decode_purchase_note(row.note)
        if (
            meta.get("supplier_tax_id") == supplier_tax_id
            and meta.get("invoice_number") == invoice_number
        ):
            return True
    return False


async def _load_purchase_by_id(
    session: AsyncSession, tenant_id: uuid.UUID, purchase_invoice_id: uuid.UUID
) -> PurchaseResponse:
    """Re-project a stored purchase from its ledger rows."""
    stmt = select(StockMovement).where(
        StockMovement.tenant_id == tenant_id,
        StockMovement.source_table == "purchase_invoices",
        StockMovement.source_id == purchase_invoice_id,
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        raise NotFoundError(
            "purchase not found",
            details={"purchase_invoice_id": str(purchase_invoice_id)},
        )
    return _project_purchase(rows)


def _project_purchase(rows: list[StockMovement]) -> PurchaseResponse:
    """Fold a list of ledger rows sharing one ``source_id`` into a purchase."""
    assert rows, "_project_purchase called with empty rows"
    head_meta = _decode_purchase_note(rows[0].note)
    total = Decimal("0")
    lines: list[PurchaseLineResponse] = []
    for row in rows:
        meta = _decode_purchase_note(row.note)
        unit_cost = Decimal(meta.get("unit_cost", "0"))
        line_total = (row.qty * unit_cost).quantize(Decimal("0.0001"))
        total += line_total
        expires_on_raw = meta.get("expires_on")
        expires_on = date.fromisoformat(expires_on_raw) if expires_on_raw else None
        lines.append(
            PurchaseLineResponse(
                ingredient_id=row.ingredient_id,
                qty=row.qty,
                unit_cost=unit_cost,
                line_total=line_total,
                lot_no=row.lot_no,
                expires_on=expires_on,
                movement_id=row.id,
            )
        )
    head = rows[0]
    invoice_date_raw = head_meta.get("invoice_date")
    invoice_date_v = (
        date.fromisoformat(invoice_date_raw) if invoice_date_raw else head.occurred_at.date()
    )
    assert head.source_id is not None
    return PurchaseResponse(
        purchase_invoice_id=head.source_id,
        store_id=head.store_id,
        supplier_name=str(head_meta.get("supplier_name", "")),
        supplier_tax_id=head_meta.get("supplier_tax_id"),
        invoice_number=str(head_meta.get("invoice_number", "")),
        invoice_date=invoice_date_v,
        received_at=head.occurred_at,
        total=total,
        notes=head_meta.get("invoice_notes"),
        lines=lines,
    )


async def create_purchase(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request: PurchaseCreateRequest,
) -> tuple[PurchaseResponse, bool]:
    """Record one supplier invoice as N ledger rows. Returns ``(resp, created)``.

    ``created`` is ``False`` when an idempotent replay matched on ``external_ref``.
    """
    await _resolve_store(session, tenant_id, request.store_id)
    # Validate every ingredient exists (404 cleanly, instead of FK IntegrityError).
    for line in request.lines:
        await _resolve_ingredient(session, tenant_id, line.ingredient_id)

    # 1) Idempotency via external_ref.
    if request.external_ref:
        existing_id = await _find_existing_purchase_by_external_ref(
            session, tenant_id, request.external_ref
        )
        if existing_id is not None:
            return await _load_purchase_by_id(session, tenant_id, existing_id), False

    # 2) Hard conflict on (supplier_tax_id, invoice_number) when no external_ref.
    if not request.external_ref and await _find_existing_invoice_conflict(
        session, tenant_id, request.supplier_tax_id, request.invoice_number
    ):
        raise ConflictError(
            "invoice already recorded",
            details={
                "code": "invoice_conflict",
                "supplier_tax_id": request.supplier_tax_id,
                "invoice_number": request.invoice_number,
            },
        )

    purchase_invoice_id = uuid7()
    received_at = request.received_at or datetime.now(UTC)

    movements: list[StockMovement] = []
    for line in request.lines:
        _assert_sign(MovementType.PURCHASE, line.qty)
        note = _encode_purchase_note(
            supplier_name=request.supplier_name,
            supplier_tax_id=request.supplier_tax_id,
            invoice_number=request.invoice_number,
            invoice_date=request.invoice_date,
            unit_cost=line.unit_cost,
            expires_on=line.expires_on,
            line_note=line.note,
            invoice_notes=request.notes,
            external_ref=request.external_ref,
        )
        mv = StockMovement(
            tenant_id=tenant_id,
            store_id=request.store_id,
            ingredient_id=line.ingredient_id,
            movement_type=MovementType.PURCHASE,
            qty=line.qty,
            source_table="purchase_invoices",
            source_id=purchase_invoice_id,
            lot_no=line.lot_no,
            occurred_at=received_at,
            note=note,
        )
        session.add(mv)
        movements.append(mv)

    await session.flush()
    return _project_purchase(movements), True


# ──────────────────────────────────────────────────────────────────────────
# Adjustment
# ──────────────────────────────────────────────────────────────────────────


async def _find_existing_adjustment_by_external_ref(
    session: AsyncSession, tenant_id: uuid.UUID, external_ref: str
) -> StockMovement | None:
    stmt = select(StockMovement).where(
        StockMovement.tenant_id == tenant_id,
        StockMovement.source_table == "stock_adjustments",
        StockMovement.movement_type.in_(
            [MovementType.ADJUSTMENT_IN, MovementType.ADJUSTMENT_OUT]
        ),
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        meta = _decode_adjustment_note(row.note)
        if meta.get("external_ref") == external_ref:
            return row
    return None


def _encode_adjustment_note(reason: str, external_ref: str | None) -> str:
    return json.dumps(
        {"kind": "adjustment", "reason": reason, "external_ref": external_ref},
        ensure_ascii=False,
        sort_keys=True,
    )


def _decode_adjustment_note(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("kind") != "adjustment":
        return {}
    return data


def _project_adjustment(row: StockMovement) -> AdjustmentResponse:
    meta = _decode_adjustment_note(row.note)
    return AdjustmentResponse(
        movement_id=row.id,
        store_id=row.store_id,
        ingredient_id=row.ingredient_id,
        delta=row.qty,
        reason=str(meta.get("reason", "")),
        occurred_at=row.occurred_at,
    )


async def create_adjustment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request: AdjustmentCreateRequest,
) -> tuple[AdjustmentResponse, bool]:
    """Record a 盤點 adjustment. Returns ``(resp, created)``."""
    await _resolve_store(session, tenant_id, request.store_id)
    await _resolve_ingredient(session, tenant_id, request.ingredient_id)

    if request.external_ref:
        existing = await _find_existing_adjustment_by_external_ref(
            session, tenant_id, request.external_ref
        )
        if existing is not None:
            return _project_adjustment(existing), False

    movement_type = (
        MovementType.ADJUSTMENT_IN if request.delta > 0 else MovementType.ADJUSTMENT_OUT
    )
    _assert_sign(movement_type, request.delta)

    occurred_at = request.occurred_at or datetime.now(UTC)
    mv = StockMovement(
        tenant_id=tenant_id,
        store_id=request.store_id,
        ingredient_id=request.ingredient_id,
        movement_type=movement_type,
        qty=request.delta,
        source_table="stock_adjustments",
        source_id=uuid7(),
        occurred_at=occurred_at,
        note=_encode_adjustment_note(request.reason, request.external_ref),
    )
    session.add(mv)
    await session.flush()
    return _project_adjustment(mv), True


# ──────────────────────────────────────────────────────────────────────────
# Reads
# ──────────────────────────────────────────────────────────────────────────


_MAX_LIMIT = 200
_PURCHASE_LIST_HARD_CAP = 500


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return 50
    if limit < 1:
        return 1
    if limit > _MAX_LIMIT:
        return _MAX_LIMIT
    return limit


def _clamp_offset(offset: int | None) -> int:
    if offset is None or offset < 0:
        return 0
    return offset


async def list_movements(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    ingredient_id: uuid.UUID | None = None,
    movement_type: MovementType | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    limit: int | None = 50,
    offset: int | None = 0,
) -> list[StockMovementResponse]:
    """Return paginated, filtered ledger rows. ``limit`` is clamped to 200."""
    if from_ts is not None and from_ts.tzinfo is None:
        raise ValidationError("from_ts must be timezone-aware")
    if to_ts is not None and to_ts.tzinfo is None:
        raise ValidationError("to_ts must be timezone-aware")
    if from_ts is not None and to_ts is not None and to_ts < from_ts:
        raise ValidationError("to_ts must be >= from_ts")

    eff_limit = _clamp_limit(limit)
    eff_offset = _clamp_offset(offset)

    stmt = select(StockMovement).where(StockMovement.tenant_id == tenant_id)
    if store_id is not None:
        stmt = stmt.where(StockMovement.store_id == store_id)
    if ingredient_id is not None:
        stmt = stmt.where(StockMovement.ingredient_id == ingredient_id)
    if movement_type is not None:
        stmt = stmt.where(StockMovement.movement_type == movement_type)
    if from_ts is not None:
        stmt = stmt.where(StockMovement.occurred_at >= from_ts)
    if to_ts is not None:
        stmt = stmt.where(StockMovement.occurred_at <= to_ts)
    stmt = stmt.order_by(StockMovement.occurred_at.desc()).limit(eff_limit).offset(eff_offset)

    rows = (await session.execute(stmt)).scalars().all()
    return [StockMovementResponse.model_validate(r) for r in rows]


async def list_purchases(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    supplier_name: str | None = None,
    limit: int | None = 50,
    offset: int | None = 0,
) -> list[PurchaseResponse]:
    """List purchases reverse-derived from ``stock_movements``.

    Grouped by ``source_id`` (the synthetic ``purchase_invoice_id``). Sorted
    by ``received_at`` desc. ``limit`` is clamped to 200; pagination via
    ``offset``. There's also a hard 500-row safety bound on the underlying
    movement scan so a wild date range can't OOM the process.
    """
    if from_date is not None and to_date is not None and to_date < from_date:
        raise ValidationError("to_date must be >= from_date")

    eff_limit = _clamp_limit(limit)
    eff_offset = _clamp_offset(offset)

    stmt = select(StockMovement).where(
        StockMovement.tenant_id == tenant_id,
        StockMovement.movement_type == MovementType.PURCHASE,
        StockMovement.source_table == "purchase_invoices",
    )
    if store_id is not None:
        stmt = stmt.where(StockMovement.store_id == store_id)
    if from_date is not None:
        stmt = stmt.where(StockMovement.occurred_at >= datetime.combine(from_date, datetime.min.time(), tzinfo=UTC))
    if to_date is not None:
        # End-of-day inclusive.
        stmt = stmt.where(StockMovement.occurred_at <= datetime.combine(to_date, datetime.max.time(), tzinfo=UTC))

    # Pull a bounded slab; group in-process. Safe because hard cap is 500.
    stmt = stmt.order_by(StockMovement.occurred_at.desc()).limit(_PURCHASE_LIST_HARD_CAP)
    rows = list((await session.execute(stmt)).scalars().all())

    # Group by source_id preserving first-seen (desc) order.
    grouped: dict[uuid.UUID, list[StockMovement]] = {}
    order: list[uuid.UUID] = []
    for row in rows:
        if row.source_id is None:
            continue
        if row.source_id not in grouped:
            grouped[row.source_id] = []
            order.append(row.source_id)
        grouped[row.source_id].append(row)

    purchases: list[PurchaseResponse] = []
    for sid in order:
        purchase = _project_purchase(grouped[sid])
        if supplier_name and supplier_name.lower() not in purchase.supplier_name.lower():
            continue
        purchases.append(purchase)

    # Apply pagination after projection so it reflects user-visible rows.
    return purchases[eff_offset : eff_offset + eff_limit]


__all__ = [
    "create_adjustment",
    "create_purchase",
    "list_movements",
    "list_purchases",
]
