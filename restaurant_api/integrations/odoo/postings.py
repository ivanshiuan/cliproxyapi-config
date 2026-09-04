"""Business-event -> Odoo double-entry journal translation (pure functions).

This module is the *domain core* of the Odoo integration. It holds no HTTP,
no I/O and no Odoo SDK: it turns a structured restaurant_api event (a purchase,
a day's sales, a waste loss, a payroll accrual) into a balanced double-entry
``JournalEntry`` that the transport layer (``client.py``) posts to Odoo.

Design rules (mirror the project's invariants):

* All money is ``Decimal`` -- never ``float``. Every input dataclass rejects
  ``float`` at construction, matching the "金錢永遠不用 float" law.
* Every ``JournalEntry`` is balanced by construction: total debit == total
  credit. ``assert_balanced`` is the guard the transport layer calls before
  it ever touches Odoo, so an unbalanced entry can never be posted.
* Account codes are *injected* via ``AccountChart``, not hard-coded magic
  strings. The defaults are placeholders -- override them to match the
  chart of accounts in your own Odoo instance.
* ``external_id`` on every entry is the restaurant_api source uuid. It is the
  idempotency key: the transport layer upserts on it so a re-run of the nightly
  sync never double-posts.

Only *summary* journal entries cross into Odoo. Line-item operational detail
(which dish, which lot) stays in restaurant_api forever -- Odoo only needs the
money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnbalancedEntryError(ValueError):
    """Raised when a JournalEntry's debits do not equal its credits."""

    def __init__(self, debit: Decimal, credit: Decimal) -> None:
        self.debit = debit
        self.credit = credit
        super().__init__(f"journal entry is unbalanced: debit={debit} != credit={credit}")


def _reject_float(name: str, value: object) -> Decimal:
    """Enforce Decimal money: raise on float, coerce int, pass Decimal through.

    Mirrors the Pydantic ``BeforeValidator`` used on the HTTP boundary, so the
    same "no float money" law holds for the plain dataclasses used here.
    """
    if isinstance(value, float):
        raise TypeError(f"{name} must be Decimal, not float: {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        return value
    raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")


# ---------------------------------------------------------------------------
# Chart of accounts (injectable -- override to match your Odoo CoA)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountChart:
    """Account *codes* used by the posting builders.

    These are sensible small-business placeholders, NOT authoritative Taiwan
    GAAP codes. Point them at the real codes in your Odoo chart of accounts
    via ``AccountChart(inventory="1310", ...)`` and store that once in config.
    """

    inventory: str = "1310"  # 存貨
    input_vat: str = "1360"  # 進項稅額 (留抵稅額)
    accounts_payable: str = "2100"  # 應付帳款
    cash: str = "1100"  # 現金 / 銀行存款
    sales_revenue: str = "4100"  # 營業收入
    output_vat: str = "2260"  # 銷項稅額
    waste_expense: str = "5810"  # 報廢損耗
    salary_expense: str = "6120"  # 薪資支出
    salary_payable: str = "2130"  # 應付薪資


DEFAULT_CHART = AccountChart()


# ---------------------------------------------------------------------------
# Neutral journal representation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JournalLine:
    """One debit-or-credit line. Exactly one of debit/credit is non-zero."""

    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str = ""
    partner_ref: str | None = None

    def __post_init__(self) -> None:
        debit = _reject_float("debit", self.debit)
        credit = _reject_float("credit", self.credit)
        object.__setattr__(self, "debit", debit)
        object.__setattr__(self, "credit", credit)
        if debit < 0 or credit < 0:
            raise ValueError("debit/credit must be non-negative")
        if debit > 0 and credit > 0:
            raise ValueError("a line cannot be both a debit and a credit")
        if debit == 0 and credit == 0:
            raise ValueError("a line must carry a non-zero debit or credit")


@dataclass(frozen=True)
class JournalEntry:
    """A balanced double-entry move, ready for the transport layer.

    ``move_type`` follows Odoo ``account.move`` semantics: ``in_invoice`` for a
    vendor bill (accounts payable), ``entry`` for a miscellaneous journal entry.
    ``journal_code`` selects which Odoo journal receives it (PUR/SAL/MISC).
    """

    external_id: str
    entry_date: date
    journal_code: str
    ref: str
    move_type: str
    lines: tuple[JournalLine, ...]
    currency_code: str = "TWD"
    partner_ref: str | None = None

    def total_debit(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), Decimal("0"))

    def total_credit(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), Decimal("0"))

    def is_balanced(self) -> bool:
        return self.total_debit() == self.total_credit()

    def assert_balanced(self) -> None:
        if not self.is_balanced():
            raise UnbalancedEntryError(self.total_debit(), self.total_credit())


# ---------------------------------------------------------------------------
# Event inputs (structured, Decimal-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PurchaseBill:
    """A received purchase -> becomes a vendor bill (accounts payable)."""

    source_id: str  # purchase_order id in restaurant_api (idempotency key)
    supplier_ref: str  # supplier code / tax id -- the AP partner
    invoice_number: str  # supplier's 統一發票 number
    occurred_on: date
    subtotal: Decimal  # excl. tax
    tax_amount: Decimal = field(default=Decimal("0"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "subtotal", _reject_float("subtotal", self.subtotal))
        object.__setattr__(self, "tax_amount", _reject_float("tax_amount", self.tax_amount))


@dataclass(frozen=True)
class DailySales:
    """A day's POS takings -> a single summary journal entry (no per-order rows)."""

    source_id: str  # e.g. f"{store_id}:{business_date}"
    occurred_on: date
    net_revenue: Decimal  # excl. tax
    tax_amount: Decimal = field(default=Decimal("0"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "net_revenue", _reject_float("net_revenue", self.net_revenue))
        object.__setattr__(self, "tax_amount", _reject_float("tax_amount", self.tax_amount))


@dataclass(frozen=True)
class WasteLoss:
    """Aggregated waste / staff-meal / tasting cost -> an expense journal entry."""

    source_id: str
    occurred_on: date
    amount: Decimal  # inventory value written off

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _reject_float("amount", self.amount))


@dataclass(frozen=True)
class PayrollAccrual:
    """A payroll period total -> a salary expense / payable accrual entry."""

    source_id: str
    occurred_on: date
    gross_wages: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "gross_wages", _reject_float("gross_wages", self.gross_wages))


# ---------------------------------------------------------------------------
# Builders  (event -> balanced JournalEntry)
# ---------------------------------------------------------------------------


def purchase_to_vendor_bill(
    bill: PurchaseBill, chart: AccountChart = DEFAULT_CHART
) -> JournalEntry:
    """進貨 -> 廠商發票 (應付).

    Dr 存貨 (subtotal), Dr 進項稅額 (tax), Cr 應付帳款-供應商 (subtotal + tax).
    """
    total = bill.subtotal + bill.tax_amount
    lines: list[JournalLine] = [
        JournalLine(chart.inventory, debit=bill.subtotal, description="進貨-存貨"),
    ]
    if bill.tax_amount > 0:
        lines.append(JournalLine(chart.input_vat, debit=bill.tax_amount, description="進項稅額"))
    lines.append(
        JournalLine(
            chart.accounts_payable,
            credit=total,
            description="應付帳款",
            partner_ref=bill.supplier_ref,
        )
    )
    entry = JournalEntry(
        external_id=bill.source_id,
        entry_date=bill.occurred_on,
        journal_code="PUR",
        ref=f"進貨 {bill.invoice_number}",
        move_type="in_invoice",
        lines=tuple(lines),
        partner_ref=bill.supplier_ref,
    )
    entry.assert_balanced()
    return entry


def daily_sales_journal(sales: DailySales, chart: AccountChart = DEFAULT_CHART) -> JournalEntry:
    """每日 POS 收班 -> 銷售彙總分錄.

    Dr 現金 (net + tax), Cr 營業收入 (net), Cr 銷項稅額 (tax).
    """
    gross = sales.net_revenue + sales.tax_amount
    lines: list[JournalLine] = [
        JournalLine(chart.cash, debit=gross, description="現金收入"),
        JournalLine(chart.sales_revenue, credit=sales.net_revenue, description="營業收入"),
    ]
    if sales.tax_amount > 0:
        lines.append(JournalLine(chart.output_vat, credit=sales.tax_amount, description="銷項稅額"))
    entry = JournalEntry(
        external_id=sales.source_id,
        entry_date=sales.occurred_on,
        journal_code="SAL",
        ref=f"每日銷售 {sales.occurred_on.isoformat()}",
        move_type="entry",
        lines=tuple(lines),
    )
    entry.assert_balanced()
    return entry


def waste_loss_journal(loss: WasteLoss, chart: AccountChart = DEFAULT_CHART) -> JournalEntry:
    """報廢 / 員工餐 / 試菜 -> 損失費用分錄.

    Dr 報廢損耗, Cr 存貨.
    """
    entry = JournalEntry(
        external_id=loss.source_id,
        entry_date=loss.occurred_on,
        journal_code="MISC",
        ref=f"報廢損耗 {loss.occurred_on.isoformat()}",
        move_type="entry",
        lines=(
            JournalLine(chart.waste_expense, debit=loss.amount, description="報廢損耗"),
            JournalLine(chart.inventory, credit=loss.amount, description="存貨沖銷"),
        ),
    )
    entry.assert_balanced()
    return entry


def payroll_journal(payroll: PayrollAccrual, chart: AccountChart = DEFAULT_CHART) -> JournalEntry:
    """薪資期結算 -> 薪資費用 / 應付薪資分錄.

    Dr 薪資支出, Cr 應付薪資.
    """
    entry = JournalEntry(
        external_id=payroll.source_id,
        entry_date=payroll.occurred_on,
        journal_code="MISC",
        ref=f"薪資計提 {payroll.occurred_on.isoformat()}",
        move_type="entry",
        lines=(
            JournalLine(chart.salary_expense, debit=payroll.gross_wages, description="薪資支出"),
            JournalLine(chart.salary_payable, credit=payroll.gross_wages, description="應付薪資"),
        ),
    )
    entry.assert_balanced()
    return entry


__all__ = [
    "DEFAULT_CHART",
    "AccountChart",
    "DailySales",
    "JournalEntry",
    "JournalLine",
    "PayrollAccrual",
    "PurchaseBill",
    "UnbalancedEntryError",
    "WasteLoss",
    "daily_sales_journal",
    "payroll_journal",
    "purchase_to_vendor_bill",
    "waste_loss_journal",
]
