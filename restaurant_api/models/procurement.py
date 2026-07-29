"""Procurement — supplier master, purchase orders, and PO lines.

This lands the tables designed in ``docs/04_data_schema.md §3`` that the stock
intake router has been faking with a synthetic ``purchase_invoice_id`` + JSON
note (see ``specs/stock_intake_router.md``). Structured purchasing is also what
the Odoo back-office sync reads: a *received* purchase order becomes a vendor
bill (accounts payable) in Odoo.

Ownership boundary (see ``docs/20``): these tables are the operations-side
source of truth for *what was purchased*. Odoo owns the *accounting* view of
the same event (the AP ledger). The two are linked by ``odoo_move_id`` /
``odoo_synced_at`` on ``purchase_orders`` — the idempotency handle that lets the
nightly sync push each PO exactly once.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    Money,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)


class PurchaseOrderStatus(enum.StrEnum):
    """Lifecycle of a purchase order.

    Only ``RECEIVED`` orders are eligible for the Odoo AP sync — a draft or
    merely-ordered PO is not yet a liability worth booking.
    """

    DRAFT = "draft"
    ORDERED = "ordered"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class Supplier(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """供應商主檔 — the party a purchase order is placed with.

    Tenant-scoped (``store_id`` optional) because a supplier typically serves
    every store of a tenant. ``code`` is the stable business key and the handle
    the Odoo sync uses as the ``res.partner`` reference.
    """

    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(8), nullable=True)  # 統一編號
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 月結30 etc.

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_suppliers_tenant_code"),)


class PurchaseOrder(TenantScopedMixin, TimestampedMixin, Base):
    """進貨單 header — one order placed with one supplier.

    Monetary totals are stored (not just derived) because the supplier's
    invoice is authoritative and may differ from the sum of lines by rounding.
    ``odoo_move_id`` / ``odoo_synced_at`` track the AP push: NULL means "not yet
    booked in Odoo", so the nightly sync query is simply
    ``received_at IS NOT NULL AND odoo_synced_at IS NULL``.
    """

    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    po_number: Mapped[str] = mapped_column(String(64), nullable=False)
    ordered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        SQLEnum(
            PurchaseOrderStatus,
            name="purchase_order_status",
            native_enum=False,
            length=16,
        ),
        nullable=False,
        default=PurchaseOrderStatus.DRAFT,
        server_default=PurchaseOrderStatus.DRAFT.value,
    )
    subtotal: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0"), server_default="0"
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0"), server_default="0"
    )
    total: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0"), server_default="0"
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="TWD", server_default="TWD"
    )
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    # ── Odoo AP sync tracking (idempotency handle + dead-letter state) ──────
    odoo_move_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    odoo_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Failure bookkeeping: each failed push bumps attempts and records the
    # error. POs at MAX_SYNC_ATTEMPTS are dead-lettered — excluded from the
    # nightly retry and surfaced by reconcile_sync_status for a human.
    odoo_sync_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    odoo_last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "po_number", name="uq_purchase_orders_tenant_po"),
        Index("ix_po_pending_sync", "tenant_id", "received_at", "odoo_synced_at"),
    )


class PurchaseOrderLine(TenantScopedMixin, TimestampedMixin, Base):
    """進貨單明細 — one ingredient line on a purchase order."""

    __tablename__ = "purchase_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)  # excl. tax
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)  # FEFO + 報廢


__all__ = [
    "PurchaseOrder",
    "PurchaseOrderLine",
    "PurchaseOrderStatus",
    "Supplier",
]
