"""Restaurant SaaS — CORE ORM models (Phase 1 MVP).

Re-exports every model class plus the shared base infrastructure. Import
from here (``from restaurant_api.models import Order``); don't reach into
sub-modules.
"""

from __future__ import annotations

from .base import (
    Base,
    Money,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)
from .cost_events import StaffMealEvent, TastingEvent, WasteEvent
from .employees import Employee, EmployeeRole
from .hr import LeaveRequest, LeaveStatus, LeaveType, Shift, TimeClock
from .inventory import Ingredient, MovementType, Recipe, StockMovement
from .menu import MenuCategory, MenuItem
from .orders import (
    DiscountKind,
    Order,
    OrderDiscount,
    OrderLine,
    OrderPayment,
    OrderStatus,
    PaymentMethod,
)
from .stores import Store
from .tenants import Tenant

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
    # inventory
    "Ingredient",
    "MovementType",
    "Recipe",
    "StockMovement",
    # orders
    "DiscountKind",
    "Order",
    "OrderDiscount",
    "OrderLine",
    "OrderPayment",
    "OrderStatus",
    "PaymentMethod",
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
]
