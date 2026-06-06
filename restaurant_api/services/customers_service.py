"""Customer master CRUD + points-history read.

Append-only ledger stays append-only — there is no service-level write
path here (orders_service writes accrual rows on close; future redemption
endpoints / nightly expiry job will write the negative rows).

Boundary rules (same as every other service):
- ``flush()`` only, never ``commit()``.
- Domain errors raise ``DomainError`` subclasses; the router doesn't
  translate exceptions.
- tenant_id is plumbed in by the router from the request DI seam.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..models import Customer, CustomerPointsLedger, CustomerTier
from ..schemas.customers import (
    CustomerCreate,
    CustomerPointsLedgerResponse,
    CustomerResponse,
    CustomerUpdate,
    PointsRedemptionRequest,
)
from .audit_service import audit

# ──────────────────────────────────────────────────────────────────────────
# Create / read / update / delete
# ──────────────────────────────────────────────────────────────────────────


async def create_customer(
    session: AsyncSession,
    payload: CustomerCreate,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> CustomerResponse:
    """Insert a new customer. 409 if phone / email / line_user_id collides
    with an existing (non-deleted) row under this tenant — the partial
    unique indexes on the table would error anyway, but checking up front
    yields a clean envelope instead of a DB-level IntegrityError."""
    await _reject_duplicate_handles(
        session,
        tenant_id=tenant_id,
        phone=payload.phone,
        email=payload.email,
        line_user_id=payload.line_user_id,
    )

    row = Customer(
        tenant_id=tenant_id,
        display_name=payload.display_name,
        phone=payload.phone,
        email=payload.email,
        line_user_id=payload.line_user_id,
        birthdate=payload.birthdate,
        tier=payload.tier,
        notes=payload.notes,
    )
    session.add(row)
    await session.flush()

    await audit(
        session,
        action="customer.created",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("customers", row.id),
        after={
            "display_name": payload.display_name,
            "tier": payload.tier.value,
            "has_line": payload.line_user_id is not None,
            "has_phone": payload.phone is not None,
        },
    )
    return CustomerResponse.model_validate(row)


async def get_customer(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> CustomerResponse:
    row = await _load_customer(session, customer_id, tenant_id)
    return CustomerResponse.model_validate(row)


async def list_customers(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    search: str | None = None,
    tier: CustomerTier | None = None,
    include_deleted: bool = False,
    limit: int = 200,
) -> list[CustomerResponse]:
    """List customers with optional filters.

    ``search`` does a case-insensitive prefix match across display_name /
    phone / line_user_id — what a counter staff types when looking someone
    up. This is the **counter lookup** API, not a marketing-side report.
    """
    stmt = select(Customer).where(Customer.tenant_id == tenant_id)
    if not include_deleted:
        stmt = stmt.where(Customer.deleted_at.is_(None))
    if tier is not None:
        stmt = stmt.where(Customer.tier == tier)
    if search:
        like = f"{search}%"
        stmt = stmt.where(
            or_(
                Customer.display_name.ilike(like),
                Customer.phone.ilike(like),
                Customer.line_user_id.ilike(like),
            )
        )
    stmt = stmt.order_by(Customer.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [CustomerResponse.model_validate(r) for r in rows]


async def patch_customer(
    session: AsyncSession,
    customer_id: uuid.UUID,
    payload: CustomerUpdate,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> CustomerResponse:
    row = await _load_customer(session, customer_id, tenant_id)

    # Collect the diff for the audit trail — only fields actually mutated.
    before: dict[str, object] = {}
    after: dict[str, object] = {}

    fields = ("display_name", "phone", "email", "line_user_id", "birthdate", "tier", "notes")
    for f in fields:
        new = getattr(payload, f)
        if new is None:
            continue
        old = getattr(row, f)
        if new == old:
            continue
        # Handles must not collide with another non-deleted customer.
        if f in ("phone", "email", "line_user_id"):
            await _reject_duplicate_handles(
                session,
                tenant_id=tenant_id,
                phone=new if f == "phone" else None,
                email=new if f == "email" else None,
                line_user_id=new if f == "line_user_id" else None,
                exclude_id=row.id,
            )
        before[f] = _serialize(old)
        after[f] = _serialize(new)
        setattr(row, f, new)

    if not after:
        # No actual change → no DB write, no audit. Caller still gets
        # a 200 with the current state (idempotent PATCH).
        return CustomerResponse.model_validate(row)

    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="customer.updated",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("customers", row.id),
        before=before,
        after=after,
    )
    return CustomerResponse.model_validate(row)


async def soft_delete_customer(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> CustomerResponse:
    """Set ``deleted_at`` — preserves the row + ledger history so revenue
    analytics still work, but removes the customer from the active list.

    Aligns with 個資法 §11 (the personal-data link can be erased while
    the financial record stays). When a hard erasure is requested, run
    the nightly purge job that zeroes the PII columns on rows where
    ``deleted_at < now() - retention_window``."""
    row = await _load_customer(session, customer_id, tenant_id)
    if row.deleted_at is not None:
        raise ConflictError(
            message="customer already deleted",
            details={"deleted_at": row.deleted_at.isoformat()},
        )
    deleted_at = datetime.now(UTC)
    row.deleted_at = deleted_at
    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="customer.deleted",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("customers", row.id),
        after={"deleted_at": deleted_at.isoformat()},
        reason=reason,
    )
    return CustomerResponse.model_validate(row)


# ──────────────────────────────────────────────────────────────────────────
# Points history
# ──────────────────────────────────────────────────────────────────────────


async def list_points_ledger(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    limit: int = 200,
) -> list[CustomerPointsLedgerResponse]:
    """Read-only history. Ordered newest-first so the counter UI shows
    the most recent earn/redeem at the top."""
    # Resolve to verify the customer exists + scope to tenant. The ledger
    # table is tenant-scoped via the customer FK; we still defensively
    # check the customer's tenant.
    await _load_customer(session, customer_id, tenant_id)
    stmt = (
        select(CustomerPointsLedger)
        .where(CustomerPointsLedger.customer_id == customer_id)
        .where(CustomerPointsLedger.tenant_id == tenant_id)
        # Sort by created_at DESC, then id DESC as a tiebreaker. uuid7 is
        # wall-clock-time-ordered (not transaction-start-time like PG's
        # now()), so when two rows share a created_at — which they will
        # when written inside the same transaction — id DESC resolves to
        # actual insertion order.
        .order_by(
            CustomerPointsLedger.created_at.desc(),
            CustomerPointsLedger.id.desc(),
        )
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [CustomerPointsLedgerResponse.model_validate(r) for r in rows]


async def redeem_points(
    session: AsyncSession,
    customer_id: uuid.UUID,
    payload: PointsRedemptionRequest,
    *,
    tenant_id: uuid.UUID,
) -> CustomerPointsLedgerResponse:
    """Spend points at the counter.

    Writes a NEGATIVE ledger row and decrements the cached
    ``points_balance``. The customer row is loaded with
    ``SELECT ... FOR UPDATE`` so two concurrent cashier requests can't
    both pass the balance check and silently overspend (no double-spend
    race even under Postgres' default Read Committed isolation).
    """
    # Row-level lock — released at transaction commit/rollback.
    stmt = (
        select(Customer)
        .where(Customer.id == customer_id, Customer.tenant_id == tenant_id)
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"customer {customer_id} not found",
            details={"customer_id": str(customer_id)},
        )
    if row.deleted_at is not None:
        raise ConflictError(
            message="cannot redeem points for a deleted customer",
            details={
                "customer_id": str(customer_id),
                "deleted_at": row.deleted_at.isoformat(),
            },
        )

    points_decimal = Decimal(payload.points)
    available = row.points_balance or Decimal("0")
    if available < points_decimal:
        raise ConflictError(
            message="insufficient points balance",
            details={
                "requested": payload.points,
                "available": str(available),
            },
        )

    ledger = CustomerPointsLedger(
        tenant_id=tenant_id,
        customer_id=row.id,
        delta=-points_decimal,  # negative for redemptions
        reason=payload.reason,
        source_order_id=payload.source_order_id,
        note=payload.note,
    )
    session.add(ledger)

    before_balance = available
    row.points_balance = available - points_decimal
    await session.flush()
    await session.refresh(ledger)

    await audit(
        session,
        action="customer.points_redeemed",
        tenant_id=tenant_id,
        actor_id=payload.actor_id,
        target=("customers", row.id),
        before={"points_balance": str(before_balance)},
        after={
            "points_balance": str(row.points_balance),
            "delta": -payload.points,
            "reason": payload.reason,
        },
        reason=payload.reason,
    )
    return CustomerPointsLedgerResponse.model_validate(ledger)


# ──────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────


async def _load_customer(
    session: AsyncSession,
    customer_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Customer:
    stmt = select(Customer).where(
        Customer.id == customer_id,
        Customer.tenant_id == tenant_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"customer {customer_id} not found",
            details={"customer_id": str(customer_id)},
        )
    return row


async def _reject_duplicate_handles(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone: str | None,
    email: str | None,
    line_user_id: str | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Check unique-per-tenant handles before insert/update. Mirrors the
    partial unique indexes on the customers table so callers get a clean
    409 envelope instead of an asyncpg ``UniqueViolationError``."""
    if not any((phone, email, line_user_id)):
        return
    conditions = []
    if phone:
        conditions.append(Customer.phone == phone)
    if email:
        conditions.append(Customer.email == email)
    if line_user_id:
        conditions.append(Customer.line_user_id == line_user_id)
    stmt = select(Customer.id, Customer.phone, Customer.email, Customer.line_user_id).where(
        Customer.tenant_id == tenant_id,
        Customer.deleted_at.is_(None),
        or_(*conditions),
    )
    if exclude_id is not None:
        stmt = stmt.where(Customer.id != exclude_id)
    hit = (await session.execute(stmt)).first()
    if hit is None:
        return
    # Pick the right field for the error envelope so the cashier sees
    # exactly which handle collided.
    collided = (
        "phone" if phone and hit.phone == phone
        else "email" if email and hit.email == email
        else "line_user_id"
    )
    raise ConflictError(
        message=f"another customer already uses this {collided}",
        details={
            "handle": collided,
            "existing_customer_id": str(hit.id),
        },
    )


def _serialize(v: object) -> object:
    """Audit-payload-friendly projection — enums to .value, dates to iso."""
    if v is None:
        return None
    if isinstance(v, CustomerTier):
        return v.value
    if hasattr(v, "isoformat"):
        return v.isoformat()  # type: ignore[union-attr]
    return v


__all__ = [
    "create_customer",
    "get_customer",
    "list_customers",
    "list_points_ledger",
    "patch_customer",
    "redeem_points",
    "soft_delete_customer",
]
