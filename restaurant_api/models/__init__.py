"""Restaurant SaaS — CORE ORM models (Phase 1 MVP).

Re-exports every model class plus the shared base infrastructure. Import
from here (``from restaurant_api.models import Order``); don't reach into
sub-modules.
"""

from __future__ import annotations

from .audit import AuditLog
from .base import (
    Base,
    Money,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)
from .campaigns import (
    CampaignPrize,
    CampaignSpin,
    CampaignStatus,
    CampaignVoucher,
    MarketingCampaign,
    VoucherStatus,
)
from .cash import CashDrawerSession
from .cost_events import StaffMealEvent, TastingEvent, WasteEvent
from .customers import (
    Customer,
    CustomerPointsLedger,
    CustomerStoredValueLedger,
    CustomerTier,
    Referral,
    ReferralStatus,
)
from .embeddings import VECTOR_DIM, Embedding
from .employees import Employee, EmployeeRole
from .hr import LeaveRequest, LeaveStatus, LeaveType, Shift, TimeClock
from .inventory import Ingredient, MovementType, Recipe, StockMovement
from .menu import (
    MenuCategory,
    MenuItem,
    MenuItemModifierGroup,
    ModifierGroup,
    ModifierOption,
    ModifierSelectionType,
)
from .orders import (
    DiscountKind,
    InvoiceMedia,
    InvoiceStatus,
    KitchenStation,
    KitchenStatus,
    Order,
    OrderDiscount,
    OrderLine,
    OrderPayment,
    OrderSource,
    OrderStatus,
    PaymentMethod,
    ServiceType,
)
from .public_holidays import PublicHoliday
from .reservations import (
    QueueStatus,
    Reservation,
    ReservationStatus,
    WalkInQueueEntry,
)
from .stores import Store
from .tables import DiningTable
from .tenants import Tenant
from .ugc import UgcKind, UgcStatus, UgcSubmission

__all__ = [  # noqa: RUF022 — order is by module, not alphabetical, for readability
    # base / shared
    "Base",
    "Money",
    "SoftDeleteMixin",
    "TenantScopedMixin",
    "TimestampedMixin",
    "uuid7",
    # tenants
    "Tenant",
    # stores
    "Store",
    # employees
    "Employee",
    "EmployeeRole",
    # menu
    "MenuCategory",
    "MenuItem",
    "MenuItemModifierGroup",
    "ModifierGroup",
    "ModifierOption",
    "ModifierSelectionType",
    # inventory
    "Ingredient",
    "MovementType",
    "Recipe",
    "StockMovement",
    # orders
    "DiscountKind",
    "OrderSource",
    "ServiceType",
    "InvoiceMedia",
    "InvoiceStatus",
    "KitchenStation",
    "KitchenStatus",
    "Order",
    "OrderDiscount",
    "OrderLine",
    "OrderPayment",
    "OrderStatus",
    "PaymentMethod",
    # reservations / queue
    "QueueStatus",
    "Reservation",
    "ReservationStatus",
    "WalkInQueueEntry",
    # cost events
    "StaffMealEvent",
    "TastingEvent",
    "WasteEvent",
    # hr
    "LeaveRequest",
    "LeaveStatus",
    "LeaveType",
    "Shift",
    "TimeClock",
    # cash drawer
    "CashDrawerSession",
    # public holiday calendar (Taiwan)
    "PublicHoliday",
    # dining tables (桌位)
    "DiningTable",
    # audit trail
    "AuditLog",
    # customers / loyalty
    "Customer",
    "CustomerTier",
    "CustomerPointsLedger",
    "CustomerStoredValueLedger",
    "Referral",
    "ReferralStatus",
    "UgcKind",
    "UgcStatus",
    "UgcSubmission",
    # marketing campaigns (wheel-spin lottery)
    "MarketingCampaign",
    "CampaignStatus",
    "CampaignPrize",
    "CampaignSpin",
    "CampaignVoucher",
    "VoucherStatus",
    # embeddings (AI data asset seed)
    "Embedding",
    "VECTOR_DIM",
]
