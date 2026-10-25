"""Owner-facing daily P&L: turns a day of real DB rows into the pure ``compute_daily_pnl`` engine.

Why this exists: ``services/calc/profit_calc.compute_daily_pnl`` was written, tested, and never
called by anything. This module is its first caller.

Design notes (read before changing the arithmetic):

* **One order at a time for discounts.** The engine's own docstring says it applies each order's
  discounts to the whole day's gross when fed many orders at once, so a multi-order day gets the
  wrong discount. Here every order's discount is resolved with ``orders_service._compute_net_revenue``
  (the same function the till uses) and only the *resolved totals* go into the engine.
* **Same till math as the POS.** due = discounted net + service charge, where service charge =
  round(pre-discount gross x rate, 2). That is ``pos_order_service.amount_due`` in PR #29. The column
  ``Order.service_charge_rate`` only exists once #29 lands, so it is read with ``getattr`` and defaults
  to 0: a charge that was never recorded is never invented.
* **VAT.** Menu prices and the service charge are VAT-inclusive (founder, 2026-10-15), so the tax is
  carved out of revenue: tax = due x r / (1 + r). ``RESTO_PNL_PRICES_INCLUDE_VAT=false`` switches it off.
* **Labor** follows docs/04 (``mv_daily_pnl``): clocked hours x hourly rate x 1.0 / 1.34 / 1.67 / 2.0, with
  ``monthly_salary / 30 / 8`` as the hourly basis for salaried staff. One deliberate difference: salaried
  staff are accrued daily (salary / days in month) because a monthly salary is not paid less on days off.
* **Fixed costs** have no table yet (``fixed_cost_items`` is designed in docs/04 but never built), so
  monthly amounts come from ``RESTO_PNL_FIXED_COSTS_MONTHLY`` (JSON) and are spread evenly per day.
  Real numbers stay out of this public repository.
* Phase 1 has no per-request tenant filtering anywhere; like the rest of the code, a store is the scope.

User-facing strings use ASCII punctuation, matching the rest of ``restaurant_api`` (ruff RUF001).
"""

from __future__ import annotations

import calendar
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.errors import DomainError, NotFoundError, ValidationError
from ..models.cost_events import StaffMealEvent, TastingEvent, WasteEvent
from ..models.employees import Employee
from ..models.hr import TimeClock
from ..models.orders import Order, OrderStatus
from ..models.stores import Store
from ..schemas.daily_pnl import DailyPnlReport, PnlLine
from . import orders_service
from .calc.profit_calc import (
    CostEvent,
    DailyPnLInput,
    FixedCostLine,
    OrderLine,
    PlatformFee,
    compute_daily_pnl,
)

TAIPEI = ZoneInfo("Asia/Taipei")

# Overtime multipliers as defined by models/hr.py::TimeClock and docs/04.
OT1_MULT = Decimal("1.34")  # first overtime tier
OT2_MULT = Decimal("1.67")  # second overtime tier
HOLIDAY_MULT = Decimal("2.0")  # working a holiday
_Q2 = Decimal("0.01")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def q2(value: Decimal) -> Decimal:
    return value.quantize(_Q2, rounding=ROUND_HALF_UP)


def till_service_charge(gross: Decimal, rate: Decimal) -> Decimal:
    """The service charge exactly as the till computes it (``pos_order_service.amount_due`` in PR #29).

    The till quantizes with Python's default decimal context, i.e. half-EVEN (banker's rounding), while the
    rest of the code base rounds half-up. On an exact tie (11.25 x 10% = 1.125) the two differ by one cent,
    which would show up here as a bogus "collected != due" difference. So mirror the till, explicitly.
    ``tests/test_daily_pnl_till_parity.py`` pins the two together as soon as #29 is in the tree.
    """
    return (gross * rate).quantize(_Q2, rounding=ROUND_HALF_EVEN)


# -----------------------------------------------------------------------------
# Settings (own class so ``config.py``, a merge hotspot, stays untouched)
# -----------------------------------------------------------------------------


class PnlSettings(BaseSettings):
    """``RESTO_PNL_*`` environment variables. Real amounts live in the host's env, not in git."""

    model_config = SettingsConfigDict(
        env_prefix="RESTO_PNL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    prices_include_vat: bool = True
    vat_rate: Decimal = Field(default=Decimal("0.05"), ge=0, lt=1)
    # {"rent": 130000, "utilities": 45000}: NT$ per month, spread over the days of the month.
    fixed_costs_monthly: dict[str, Decimal] = Field(default_factory=dict)
    # Employer-side labor insurance / pension as a ratio of wages, e.g. 0.17. 0 = not included.
    labor_on_cost_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)

    @field_validator("fixed_costs_monthly")
    @classmethod
    def _no_negative_amounts(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        negative = sorted(name for name, amount in value.items() if amount < 0)
        if negative:  # silently ignoring a negative rent would look like "rent is included"
            raise ValueError(f"amounts cannot be negative: {', '.join(negative)}")
        return value


class PnlNotConfiguredError(DomainError):
    """A RESTO_PNL_* value is malformed: name the variable and show the fix instead of an opaque 500."""

    code = "PNL_SETTINGS_INVALID"
    status_code = 503


# What a person editing .env by hand most likely got wrong, per setting.
_SETTING_HINTS = {
    "fixed_costs_monthly": (
        '必須是 JSON, 金額是數字, 例如 {"租金": 100000, "水電": 30000}'
        "(引號要半形 \", 最後一項後面不能有逗號, 金額不能是負數)"
    ),
    "labor_on_cost_rate": "必須是 0 到 1 之間的小數, 17% 要寫 0.17",
    "vat_rate": "必須是小於 1 的小數, 營業稅 5% 要寫 0.05",
    "prices_include_vat": "只能寫 true 或 false",
}


def load_settings() -> PnlSettings:
    """``PnlSettings()``, but a bad hand-edited value becomes a message that says which one and why."""
    try:
        return PnlSettings()
    except SettingsError as exc:  # JSON that does not parse; the message names the field
        found = re.search(r'field "(\w+)"', str(exc))
        fields = [found.group(1)] if found else []
        reason = str(exc.__cause__ or exc)
    except PydanticValidationError as exc:  # parsed, but not acceptable
        errors = exc.errors()
        fields = list(dict.fromkeys(str(e["loc"][0]) for e in errors if e["loc"]))
        reason = "; ".join(f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in errors)
    names = [f"RESTO_PNL_{f.upper()}" for f in fields] or ["RESTO_PNL_*"]
    hints = " ".join(f"{n}: {_SETTING_HINTS.get(f, '值不正確')}。" for n, f in zip(names, fields or [""], strict=False))
    raise PnlNotConfiguredError(
        message=f"每日損益的設定有誤, 請修正後重啟 api 容器。{hints}",
        details={"settings": names, "reason": reason},
    )


# -----------------------------------------------------------------------------
# Plain facts: what the database says about one day (no ORM objects past this line)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderFacts:
    gross: Decimal  # sum of line totals
    discounted: Decimal  # after the discount stack
    service_charge: Decimal
    paid: Decimal
    payment_fees: dict[str, Decimal] = field(default_factory=dict)  # method -> fee
    cogs_actual: Decimal = _ZERO
    cogs_theoretical: Decimal = _ZERO
    lines_without_cost: int = 0


@dataclass(frozen=True)
class StaffDay:
    name: str
    hourly_wage: Decimal
    monthly_salary: Decimal | None
    employed: bool  # on the payroll on this date
    regular: Decimal = _ZERO
    ot1: Decimal = _ZERO
    ot2: Decimal = _ZERO
    holiday: Decimal = _ZERO


@dataclass(frozen=True)
class DayFacts:
    store_id: uuid.UUID
    store_name: str
    business_date: date
    orders: list[OrderFacts]
    open_orders: int = 0
    voided_orders: int = 0
    waste: Decimal = _ZERO
    staff_meal: Decimal = _ZERO
    tasting: Decimal = _ZERO
    staff: list[StaffDay] = field(default_factory=list)
    open_shifts: int = 0


# -----------------------------------------------------------------------------
# Pure arithmetic
# -----------------------------------------------------------------------------


def days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def labor_lines(facts: DayFacts) -> tuple[list[PnlLine], list[str]]:
    """Per-person labor cost for the day, plus warnings about people we could not price."""
    lines: list[PnlLine] = []
    warnings: list[str] = []
    dim = Decimal(days_in_month(facts.business_date))
    for s in facts.staff:
        hours = s.regular + s.ot1 + s.ot2 + s.holiday
        if s.monthly_salary is not None and s.monthly_salary > _ZERO:
            accrual = s.monthly_salary / dim if s.employed else _ZERO
            basis = s.monthly_salary / Decimal(240)  # labor law: hourly basis = monthly / 30 / 8
            premium = basis * (s.ot1 * OT1_MULT + s.ot2 * OT2_MULT + s.holiday * (HOLIDAY_MULT - _ONE))
            cost = accrual + premium
            note = f"月薪制: 月薪/{dim} 每日攤提" + (" + 加班費" if premium > _ZERO else "")
        else:
            if hours == _ZERO:
                continue  # hourly and did not work: no cost, no clutter
            if s.hourly_wage <= _ZERO:
                warnings.append(f"員工「{s.name}」有 {hours} 小時工時但沒有設定時薪或月薪, 這些工時沒有計入人事成本。")
                continue
            cost = s.hourly_wage * (s.regular + s.ot1 * OT1_MULT + s.ot2 * OT2_MULT + s.holiday * HOLIDAY_MULT)
            note = f"時薪制: {hours} 小時"
        lines.append(PnlLine(label=s.name, amount=q2(cost), note=note))
    return lines, warnings


def compose(facts: DayFacts, settings: PnlSettings) -> DailyPnlReport:
    """Turn a day's facts into the report. Pure: no I/O, so every number is unit-testable."""
    assumptions: list[str] = []
    warnings: list[str] = []

    # -- revenue: the till's math, summed --
    gross = sum((o.gross for o in facts.orders), _ZERO)
    discount_total = sum((o.gross - o.discounted for o in facts.orders), _ZERO)
    service_charge = sum((o.service_charge for o in facts.orders), _ZERO)
    billed = sum((o.discounted + o.service_charge for o in facts.orders), _ZERO)
    if settings.prices_include_vat:
        vat = billed * settings.vat_rate / (_ONE + settings.vat_rate)
        revenue = billed - vat
        assumptions.append(f"菜單價與服務費皆含營業稅 {settings.vat_rate * 100:.0f}%: 營業稅款不算營收, 已從應收中扣出。")
    else:
        vat = _ZERO
        revenue = billed
        assumptions.append("菜單價不含營業稅: 營業稅由發票另計, 不在訂單金額內, 營收 = 應收。")
    assumptions.append("服務費視為公司營收(沒有代收轉發給員工)。")

    # -- cost of goods: sold dishes only; waste / staff meals / tasting are separate lines --
    cogs_actual = sum((o.cogs_actual for o in facts.orders), _ZERO)
    cogs_theoretical = sum((o.cogs_theoretical for o in facts.orders), _ZERO)
    missing_cost = sum(o.lines_without_cost for o in facts.orders)
    assumptions.append("食材成本採 BOM 配方的理論成本; 訂單行目前沒有「實際成本」量測, 損耗另列於報廢/員工餐/試菜。")
    if missing_cost:
        warnings.append(f"{missing_cost} 個訂單品項沒有成本資料(沒有配方), 這些品項的成本被當成 0, 毛利偏高。")

    # -- platform / card fees --
    fee_by_method: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for o in facts.orders:
        for method, fee in o.payment_fees.items():
            fee_by_method[method] += fee
    platform_fees = [
        PlatformFee(source=f"收款手續費({method})", amount=fee)
        for method, fee in sorted(fee_by_method.items())
        if fee > _ZERO
    ]

    # -- labor --
    staff_lines, labor_warnings = labor_lines(facts)
    warnings.extend(labor_warnings)
    wages = sum((ln.amount for ln in staff_lines), _ZERO)
    on_cost = wages * settings.labor_on_cost_rate
    labor_total = wages + on_cost
    labor_out = list(staff_lines)
    if on_cost > _ZERO:
        labor_out.append(
            PnlLine(
                label="雇主負擔(勞健保/勞退)",
                amount=q2(on_cost),
                note=f"薪資 x {settings.labor_on_cost_rate * 100:.1f}%",
            )
        )
    else:
        warnings.append(
            "人事成本沒有計入雇主負擔的勞健保與勞退(通常約薪資的 15-20%), 淨利因此偏高。"
            "可用 RESTO_PNL_LABOR_ON_COST_RATE 設定。"
        )
    assumptions.append(
        "人事成本: 時薪制 = 打卡工時 x 時薪 x 勞基法倍率(1.0/1.34/1.67/2.0); 月薪制 = 月薪/當月天數逐日攤提 + 加班費。"
    )
    if facts.open_shifts:
        warnings.append(f"{facts.open_shifts} 筆打卡還沒下班, 這些工時沒有計入人事成本。")

    # -- fixed costs: monthly amounts spread evenly over the month --
    dim = Decimal(days_in_month(facts.business_date))
    fixed_lines = [
        FixedCostLine(name=name, daily_amount=amount / dim)
        for name, amount in sorted(settings.fixed_costs_monthly.items())
        if amount > _ZERO
    ]
    names = " ".join(settings.fixed_costs_monthly).lower()
    if not settings.fixed_costs_monthly:
        warnings.append("沒有設定任何固定成本(RESTO_PNL_FIXED_COSTS_MONTHLY), 淨利沒有扣租金與水電, 嚴重偏高。")
    else:
        # Labels are free text the owner chose, in Chinese or English: accept both for the two basics.
        if not any(k in names for k in ("租", "rent", "lease")):
            warnings.append("固定成本裡沒有看到「租金」。")
        if not any(k in names for k in ("水電", "水費", "電費", "瓦斯", "utilit", "electric", "water", "gas")):
            warnings.append("固定成本裡沒有看到「水電」。")
    warnings.append("沒有計入設備與裝潢折舊、營業稅申報與所得稅。")
    assumptions.append(f"固定成本只含你設定的項目, 月金額 / 當月 {dim} 天, 平均攤到每天。")

    # -- the engine: one formula for "what's left" (profit_calc) --
    out = compute_daily_pnl(
        DailyPnLInput(
            business_date=facts.business_date,
            store_id=str(facts.store_id),
            # Discounts, service charge and VAT are already resolved per order above, so the engine
            # receives one ex-VAT revenue line and no discounts (its multi-order discount caveat
            # therefore cannot bite).
            orders=[
                OrderLine(
                    item_id="day-revenue-ex-vat",
                    qty=_ONE,
                    unit_price=revenue,
                    cogs_actual=cogs_actual,
                    cogs_theoretical=cogs_theoretical,
                )
            ]
            if facts.orders
            else [],
            discounts=[],
            cost_events=[
                CostEvent(kind="waste", cost=facts.waste),
                CostEvent(kind="staff_meal", cost=facts.staff_meal),
                CostEvent(kind="tasting", cost=facts.tasting),
            ],
            platform_fees=platform_fees,
            fixed_costs=fixed_lines,
            labor_cost=labor_total,
        )
    )

    # -- reconciliation: money actually collected vs what the till says is owed --
    payments_total = sum((o.paid for o in facts.orders), _ZERO)
    payments_diff = payments_total - billed
    if facts.orders and abs(payments_diff) >= _Q2:
        direction = "少於" if payments_diff < 0 else "多於"
        warnings.append(
            f"收款合計 {q2(payments_total)} {direction}應收 {q2(billed)}(差 {q2(abs(payments_diff))}): "
            "請檢查是否有訂單漏記收款, 或有找零/小費。"
        )
    if facts.orders and service_charge == _ZERO:
        warnings.append(
            "服務費合計是 0: 若店裡結帳會加 10% 服務費, 請確認 POS 有設定服務費率(目前版本沒有這個欄位時一律視為 0)。"
        )
    if facts.open_orders:
        warnings.append(f"還有 {facts.open_orders} 張訂單沒結帳, 不在這份損益內。")
    if facts.voided_orders:
        warnings.append(f"{facts.voided_orders} 張訂單已作廢或退款, 不計入營收。")

    net = out.net_profit_real
    return DailyPnlReport(
        store_id=facts.store_id,
        store_name=facts.store_name,
        business_date=facts.business_date,
        order_count=len(facts.orders),
        open_order_count=facts.open_orders,
        voided_order_count=facts.voided_orders,
        menu_gross=q2(gross),
        discount_total=q2(discount_total),
        service_charge=q2(service_charge),
        billed_incl_vat=q2(billed),
        vat=q2(vat),
        revenue_ex_vat=out.net_revenue,
        cogs=out.cogs_actual_total,
        cost_waste=out.cost_waste,
        cost_staff_meal=out.cost_staff_meal,
        cost_tasting=out.cost_tasting,
        payment_fees=out.platform_fee_total,
        gross_profit=out.gross_profit_real,
        labor_cost=out.labor_cost,
        labor_lines=labor_out,
        fixed_cost_total=out.fixed_cost_total,
        fixed_cost_lines=[PnlLine(label=f.name, amount=q2(f.daily_amount)) for f in fixed_lines],
        net_profit=net,
        net_margin=(net / out.net_revenue).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if out.net_revenue > _ZERO
        else None,
        payments_total=q2(payments_total),
        payments_diff=q2(payments_diff),
        assumptions=assumptions,
        warnings=warnings,
    )


# -----------------------------------------------------------------------------
# Database -> facts
# -----------------------------------------------------------------------------


def _day_window_utc(d: date) -> tuple[datetime, datetime]:
    """[00:00, 24:00) Asia/Taipei of ``d`` as UTC instants."""
    start = datetime.combine(d, time.min, tzinfo=TAIPEI)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


async def resolve_store(session: AsyncSession, store_id: uuid.UUID | None) -> Store:
    """The named store, or the only active one. Anything else must be spelled out by the caller."""
    if store_id is not None:
        store = await session.get(Store, store_id)
        if store is None or store.deleted_at is not None:
            raise NotFoundError(message=f"找不到門市 {store_id}")
        return store
    stores = (
        (
            await session.execute(
                select(Store).where(Store.is_active.is_(True), Store.deleted_at.is_(None)).order_by(Store.created_at)
            )
        )
        .scalars()
        .all()
    )
    if len(stores) == 1:
        return stores[0]
    raise ValidationError(
        message="請用 store_id 指定門市" if stores else "系統裡還沒有任何門市",
        details={"stores": [{"id": str(s.id), "name": s.name} for s in stores]},
    )


def _order_facts(order: Order) -> OrderFacts:
    gross = sum((ln.line_total for ln in order.lines), _ZERO)
    discounted = orders_service._compute_net_revenue(order)
    rate = getattr(order, "service_charge_rate", None) or _ZERO
    charge = till_service_charge(gross, rate)
    fees: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for p in order.payments:
        fees[p.method.value] += p.fee_amount or _ZERO
    actual = theoretical = _ZERO
    uncosted = 0
    for ln in order.lines:
        theo = ln.cogs_theoretical
        act = ln.cogs_actual if ln.cogs_actual is not None else theo
        if act is None:
            uncosted += 1
            continue
        actual += act
        theoretical += theo if theo is not None else act
    return OrderFacts(
        gross=gross,
        discounted=discounted,
        service_charge=charge,
        paid=sum((p.amount for p in order.payments), _ZERO),
        payment_fees=dict(fees),
        cogs_actual=actual,
        cogs_theoretical=theoretical,
        lines_without_cost=uncosted,
    )


async def load_day_facts(session: AsyncSession, store: Store, business_date: date) -> DayFacts:
    orders = (
        (
            await session.execute(
                select(Order)
                .where(
                    Order.store_id == store.id,
                    Order.business_date == business_date,
                    Order.deleted_at.is_(None),
                )
                .options(
                    selectinload(Order.lines),
                    selectinload(Order.discounts),
                    selectinload(Order.payments),
                )
                # Money report: read the rows as they are now. Without this, an order this session loaded
                # earlier (say, a checkout in the same request) keeps its stale in-memory payments list.
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    closed = [o for o in orders if o.status == OrderStatus.CLOSED]
    open_orders = sum(1 for o in orders if o.status == OrderStatus.OPEN)
    voided = sum(1 for o in orders if o.status in (OrderStatus.VOIDED, OrderStatus.REFUNDED))

    start, end = _day_window_utc(business_date)

    async def total(model: Any, column: Any) -> Decimal:
        value = await session.scalar(
            select(func.coalesce(func.sum(column), 0)).where(
                model.store_id == store.id, model.occurred_at >= start, model.occurred_at < end
            )
        )
        return Decimal(value or 0)

    waste = await total(WasteEvent, WasteEvent.cost)
    staff_meal = await total(StaffMealEvent, StaffMealEvent.total_cost)
    tasting = await total(TastingEvent, TastingEvent.total_cost)

    clocks = (
        (
            await session.execute(
                select(TimeClock).where(
                    TimeClock.store_id == store.id, TimeClock.clock_in >= start, TimeClock.clock_in < end
                )
            )
        )
        .scalars()
        .all()
    )
    open_shifts = sum(1 for c in clocks if c.clock_out is None)
    hours: dict[uuid.UUID, list[Decimal]] = defaultdict(lambda: [_ZERO, _ZERO, _ZERO, _ZERO])
    for c in clocks:
        if c.clock_out is None:
            continue
        h = hours[c.employee_id]
        h[0] += c.regular_hours or _ZERO
        h[1] += c.overtime_tier1_hours or _ZERO
        h[2] += c.overtime_tier2_hours or _ZERO
        h[3] += c.holiday_hours or _ZERO

    employees = (
        (await session.execute(select(Employee).where(Employee.store_id == store.id, Employee.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    staff: list[StaffDay] = []
    for e in employees:
        employed = e.hired_on <= business_date and (e.terminated_on is None or e.terminated_on >= business_date)
        if not employed and e.id not in hours:
            continue
        h = hours.get(e.id, [_ZERO, _ZERO, _ZERO, _ZERO])
        staff.append(
            StaffDay(
                name=e.full_name,
                hourly_wage=e.hourly_wage,
                monthly_salary=e.monthly_salary,
                employed=employed,
                regular=h[0],
                ot1=h[1],
                ot2=h[2],
                holiday=h[3],
            )
        )

    return DayFacts(
        store_id=store.id,
        store_name=store.name,
        business_date=business_date,
        orders=[_order_facts(o) for o in closed],
        open_orders=open_orders,
        voided_orders=voided,
        waste=waste,
        staff_meal=staff_meal,
        tasting=tasting,
        staff=staff,
        open_shifts=open_shifts,
    )


async def build_daily_pnl(
    session: AsyncSession,
    *,
    store_id: uuid.UUID | None,
    business_date: date,
    settings: PnlSettings | None = None,
) -> DailyPnlReport:
    settings = settings or load_settings()  # fail on a bad .env value before touching the database
    store = await resolve_store(session, store_id)
    facts = await load_day_facts(session, store, business_date)
    return compose(facts, settings)


__all__ = [
    "DayFacts",
    "OrderFacts",
    "PnlNotConfiguredError",
    "PnlSettings",
    "StaffDay",
    "build_daily_pnl",
    "compose",
    "labor_lines",
    "load_settings",
    "resolve_store",
]
