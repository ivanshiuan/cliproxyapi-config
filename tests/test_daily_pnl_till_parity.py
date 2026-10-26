"""Contract: the daily P&L does the POS till's order arithmetic, cent for cent.

PR #29 ships ``pos_order_service.amount_due`` as "the single source of the till math": the quote, the
checkout and the sales reports all call it. The P&L re-derives the same four numbers from stored rows
(``daily_pnl_service._order_facts``) so that it also runs on a schema without #29. If the two ever
disagree, the owner's report silently stops matching the cash drawer, so this test pins them together.

It skips itself while #29 is not in the tree and then guards every later change to either side.
Cases are in-memory ORM objects (no database): the arithmetic is the whole point.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from restaurant_api.models import Order, OrderDiscount, OrderLine
from restaurant_api.models.orders import DiscountKind
from restaurant_api.services.daily_pnl_service import _order_facts


@pytest.fixture(scope="module")
def amount_due():
    return pytest.importorskip("restaurant_api.services.pos_order_service").amount_due


def _order(lines, discounts, rate) -> Order:
    order = Order()  # never flushed
    order.service_charge_rate = None if rate is None else D(rate)
    order.lines = [OrderLine(qty=D(q), unit_price=D(p), line_total=D(t)) for q, p, t in lines]
    order.discounts = [OrderDiscount(kind=DiscountKind(kind), value=D(v)) for kind, v in discounts]
    order.payments = []
    return order


# (lines as (qty, unit_price, line_total), discounts as (kind, value), service-charge rate)
CASES = [
    pytest.param([("4", "250", "1000")], [], "0.10", id="plain-10pct"),
    pytest.param([("4", "250", "1000")], [("percent", "0.10")], "0.10", id="percent-off-charge-stays-on-gross"),
    pytest.param([("1", "999", "999")], [("amount", "50")], "0.05", id="amount-off-5pct"),
    pytest.param([("2", "120", "240")], [("comp", "100")], "0.10", id="comp"),
    pytest.param([("2", "120", "240")], [], "0", id="zero-rate"),
    pytest.param([("2", "120", "240")], [], None, id="no-rate"),
    # exact ties: banker's rounding at the till, which the report must reproduce
    pytest.param([("1", "11.25", "11.25")], [], "0.10", id="tie-11.25"),
    pytest.param([("1", "12.25", "12.25")], [], "0.10", id="tie-12.25"),
    pytest.param([("1", "12.35", "12.35")], [], "0.10", id="tie-12.35"),
    pytest.param([("0.25", "45", "11.25"), ("3", "35", "105")], [], "0.10", id="tie-two-lines-116.25"),
    pytest.param([("0.25", "45", "11.25"), ("3", "35", "105")], [("percent", "0.15")], "0.10", id="tie-and-discount"),
]


@pytest.mark.parametrize(("lines", "discounts", "rate"), CASES)
def test_report_math_equals_till_math(amount_due, lines, discounts, rate):
    order = _order(lines, discounts, rate)
    gross, discount_total, charge, due = amount_due(order)
    facts = _order_facts(order)

    assert facts.gross == gross
    assert facts.gross - facts.discounted == discount_total
    assert facts.service_charge == charge
    assert facts.discounted + facts.service_charge == due
