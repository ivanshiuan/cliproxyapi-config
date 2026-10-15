"""Pure arithmetic of the daily P&L (``daily_pnl_service.compose``): no DB, no HTTP.

Every expected number below was computed independently (plain Decimal arithmetic, not by calling
the code under test), so these tests check the wiring of the formula and not just its consistency.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal as D

from restaurant_api.services.daily_pnl_html import render_html
from restaurant_api.services.daily_pnl_service import (
    DayFacts,
    OrderFacts,
    PnlSettings,
    StaffDay,
    compose,
)

STORE = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OCT_14 = date(2026, 10, 14)  # a 31-day month


def settings(**over) -> PnlSettings:
    """Every field explicit, so a stray RESTO_PNL_* variable in the host environment cannot leak in."""
    base = dict(
        prices_include_vat=True,
        vat_rate=D("0.05"),
        fixed_costs_monthly={},
        labor_on_cost_rate=D("0"),
    )
    base.update(over)
    return PnlSettings(**base)


def order(gross, discounted=None, *, charge=D("0"), paid=None, fees=None, cogs=D("0"), uncosted=0) -> OrderFacts:
    discounted = D(gross) if discounted is None else D(discounted)
    return OrderFacts(
        gross=D(gross),
        discounted=discounted,
        service_charge=D(charge),
        paid=discounted + D(charge) if paid is None else D(paid),
        payment_fees=fees or {},
        cogs_actual=D(cogs),
        cogs_theoretical=D(cogs),
        lines_without_cost=uncosted,
    )


def facts(orders, *, staff=(), when=OCT_14, **kw) -> DayFacts:
    return DayFacts(store_id=STORE, store_name="Test Store", business_date=when, orders=list(orders), staff=list(staff), **kw)


def busy_day() -> DayFacts:
    """A day with two orders (one discounted), 10% service charge, waste/meal/tasting, two staff."""
    return facts(
        [
            # 30000 menu, 10% off -> 27000, service 10% of the PRE-discount 30000 = 3000, card fee 780
            order(30000, 27000, charge=D("3000"), fees={"credit": D("780")}, cogs=D("9000")),
            order(20000, 20000, charge=D("2000"), cogs=D("6000")),
        ],
        staff=[
            StaffDay("amin", D("200"), None, True, regular=D("8"), ot1=D("1")),
            StaffDay("manager", D("0"), D("62000"), True, ot1=D("2")),
            StaffDay("idle-hourly", D("190"), None, True),
        ],
        waste=D("400"),
        staff_meal=D("600"),
        tasting=D("200"),
    )


def test_busy_day_every_line():
    r = compose(busy_day(), settings(fixed_costs_monthly={"rent": D("62000"), "utilities": D("31000")}))

    # revenue: due = (27000 + 3000) + (20000 + 2000); VAT 5% is inside it
    assert (r.menu_gross, r.discount_total, r.service_charge) == (D("50000.00"), D("3000.00"), D("5000.00"))
    assert r.billed_incl_vat == D("52000.00")
    assert r.vat == D("2476.19")
    assert r.revenue_ex_vat == D("49523.81")

    # costs and profit
    assert r.cogs == D("15000.00")
    assert (r.cost_waste, r.cost_staff_meal, r.cost_tasting) == (D("400.00"), D("600.00"), D("200.00"))
    assert r.payment_fees == D("780.00")
    assert r.gross_profit == D("33323.81")
    assert r.fixed_cost_total == D("3000.00")
    assert r.net_profit == D("24983.48")
    assert r.net_margin == D("0.5045")

    # labor: hourly 200 x (8 + 1 x 1.34); salaried 62000/31 accrual + 62000/240 x 2 x 1.34
    by_name = {ln.label: ln.amount for ln in r.labor_lines}
    assert by_name == {"amin": D("1868.00"), "manager": D("2692.33")}  # idle hourly staff: no line
    assert r.labor_cost == D("4560.33")

    # books and cash agree
    assert r.payments_total == D("52000.00") and r.payments_diff == D("0.00")
    assert not any("收款合計" in w for w in r.warnings)


def test_service_charge_is_on_the_pre_discount_total():
    """A 10% discount must not shrink the service charge: the till charges rate x gross."""
    r = compose(facts([order(1000, 900, charge=D("100"))]), settings())
    assert r.billed_incl_vat == D("1000.00")  # 900 discounted + 100 charge, NOT 900 + 90


def test_vat_can_be_switched_off():
    r = compose(facts([order(1050, charge=D("0"))]), settings(prices_include_vat=False))
    assert r.vat == D("0.00") and r.revenue_ex_vat == r.billed_incl_vat == D("1050.00")
    assert any("不含營業稅" in a for a in r.assumptions)


def test_vat_included_carves_out_one_twenty_first():
    r = compose(facts([order(1050)]), settings())
    assert r.vat == D("50.00") and r.revenue_ex_vat == D("1000.00")


def test_no_orders_still_costs_money():
    """A closed day still pays rent and salaried staff: net is negative, margin is undefined."""
    r = compose(
        facts([], staff=[StaffDay("manager", D("0"), D("31000"), True)]),
        settings(fixed_costs_monthly={"rent": D("31000")}),
    )
    assert r.order_count == 0 and r.revenue_ex_vat == D("0.00")
    assert r.labor_cost == D("1000.00") and r.fixed_cost_total == D("1000.00")
    assert r.net_profit == D("-2000.00") and r.net_margin is None


def test_salary_is_spread_over_the_days_of_that_month():
    feb = date(2026, 2, 10)  # 28 days
    r = compose(facts([], when=feb, staff=[StaffDay("m", D("0"), D("28000"), True)]), settings())
    assert r.labor_cost == D("1000.00")  # 28000 / 28
    r = compose(facts([], staff=[StaffDay("m", D("0"), D("30000"), True)]), settings())
    assert r.labor_cost == D("967.74")  # 30000 / 31


def test_salaried_person_not_on_payroll_that_day_costs_nothing():
    r = compose(facts([], staff=[StaffDay("left", D("0"), D("31000"), False)]), settings())
    assert r.labor_cost == D("0.00")


def test_holiday_hours_pay_double_for_hourly_and_one_extra_for_salaried():
    hourly = compose(facts([], staff=[StaffDay("h", D("200"), None, True, holiday=D("8"))]), settings())
    assert hourly.labor_cost == D("3200.00")  # 200 x 8 x 2.0
    salaried = compose(facts([], staff=[StaffDay("s", D("0"), D("24000"), True, holiday=D("8"))]), settings())
    # accrual 24000/31 = 774.19 + extra 1x on 8h at 24000/240 = 100/h -> 800
    assert salaried.labor_cost == D("1574.19")


def test_worked_hours_without_any_wage_are_flagged_not_silently_free():
    r = compose(facts([], staff=[StaffDay("nowage", D("0"), None, True, regular=D("8"))]), settings())
    assert r.labor_cost == D("0.00")
    assert any("nowage" in w and "沒有設定時薪" in w for w in r.warnings)


def test_employer_on_cost_is_an_explicit_line_when_configured():
    r = compose(
        facts([], staff=[StaffDay("h", D("100"), None, True, regular=D("10"))]),
        settings(labor_on_cost_rate=D("0.17")),
    )
    assert r.labor_cost == D("1170.00")  # 1000 wages + 17%
    assert r.labor_lines[-1].amount == D("170.00")
    assert not any("雇主負擔" in w for w in r.warnings)


def test_missing_on_cost_is_a_warning_by_default():
    r = compose(facts([]), settings())
    assert any("雇主負擔" in w for w in r.warnings)


def test_fixed_cost_hints():
    none = compose(facts([]), settings())
    assert any("沒有設定任何固定成本" in w for w in none.warnings)
    rent_only = compose(facts([]), settings(fixed_costs_monthly={"rent-租金": D("31000")}))
    assert any("水電" in w for w in rent_only.warnings) and not any("看不到" in w for w in rent_only.warnings)
    both = compose(facts([]), settings(fixed_costs_monthly={"租金": D("1"), "水電": D("1")}))
    assert not any("固定成本裡沒有看到" in w for w in both.warnings)


def test_payment_mismatch_is_reported_with_direction():
    short = compose(facts([order(1000, paid=900)]), settings())
    assert short.payments_diff == D("-100.00")
    assert any("少於應收" in w for w in short.warnings)
    over = compose(facts([order(1000, paid=1100)]), settings())
    assert any("多於應收" in w for w in over.warnings)


def test_zero_service_charge_is_called_out():
    r = compose(facts([order(1000)]), settings())
    assert any("服務費合計是 0" in w for w in r.warnings)


def test_orders_without_cost_data_are_counted():
    r = compose(facts([order(1000, uncosted=3)]), settings())
    assert any("3 個訂單品項沒有成本資料" in w for w in r.warnings)


def test_open_and_voided_orders_are_reported_not_counted():
    r = compose(facts([order(1000)], open_orders=2, voided_orders=1, open_shifts=1), settings())
    assert (r.order_count, r.open_order_count, r.voided_order_count) == (1, 2, 1)
    joined = " ".join(r.warnings)
    assert "2 張訂單沒結帳" in joined and "1 張訂單已作廢或退款" in joined and "1 筆打卡還沒下班" in joined


def test_money_is_decimal_never_float():
    r = compose(busy_day(), settings())
    for name, value in r.model_dump().items():
        assert not isinstance(value, float), name


def test_settings_parse_fixed_costs_from_env(monkeypatch):
    monkeypatch.setenv("RESTO_PNL_FIXED_COSTS_MONTHLY", '{"租金": 130000, "水電": 45000.5}')
    monkeypatch.setenv("RESTO_PNL_LABOR_ON_COST_RATE", "0.17")
    s = PnlSettings()
    assert s.fixed_costs_monthly == {"租金": D("130000"), "水電": D("45000.5")}
    assert s.labor_on_cost_rate == D("0.17") and s.prices_include_vat is True


def test_html_escapes_everything_from_configuration():
    evil = "<script>alert(1)</script>"
    r = compose(facts([order(1050)]), settings(fixed_costs_monthly={evil: D("3100")}))
    page = render_html(r)
    assert "<script" not in page and "&lt;script&gt;" in page
    assert "今日估算淨利" in page and "NT$" in page and "Test Store" in page
