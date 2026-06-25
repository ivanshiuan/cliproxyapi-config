#!/usr/bin/env python3
"""開店單位經濟試算器（docs/19 §3 的可執行版）。

把開店營運藍圖的紙上財務模型變成可跑的計算器：改幾個假設，立刻看到
月損益（現金/會計兩視角）、損益兩平、回本期、敏感度。

設計原則（呼應 CLAUDE.md 鐵律）：
  - 所有金錢用 ``decimal.Decimal``，永不 float。
  - 純標準庫（decimal / dataclasses / argparse），無第三方依賴，任何地方都能跑。
  - 預設值 = docs/19 §0 假設參數表，跑起來即重現藍圖 §3 的 worked example，
    確保「文件數字」與「工具數字」永遠一致（這支腳本是 §3 的單一真相來源）。

用法：
  python3 scripts/launch_model.py                    # 跑三個內建情境 + 基準敏感度
  python3 scripts/launch_model.py --turns 3.5 --ticket 220 --fte 3.5 --rent 50000
  python3 scripts/launch_model.py --json             # 機器可讀輸出
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

D = Decimal


def _money(x: Decimal) -> Decimal:
    """量化到整數元（TWD 收銀無小數）。"""
    return x.quantize(D("1"), rounding=ROUND_HALF_UP)


def _pct(x: Decimal) -> Decimal:
    return (x * D("100")).quantize(D("0.1"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# 假設（docs/19 §0；frozen，改值用 replace()）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assumptions:
    name: str = "基準"
    # 營收驅動
    seats: int = 24                       # A4 座位
    turns_per_day: Decimal = D("3.0")     # 翻桌率：坐下 1.8–2.2 / 快餐 2.5–3.5
    avg_ticket_incl_tax: Decimal = D("180")  # A6 客單（含 5% 營業稅）
    days_per_month: int = 26              # A10
    delivery_share: Decimal = D("0.25")   # A11 外送占比
    # 成本率 / 固定成本
    food_cost_rate: Decimal = D("0.35")   # A7
    fte: Decimal = D("5")                 # A8（含老闆）
    cost_per_fte: Decimal = D("38000")    # A9（含勞健保）
    rent: Decimal = D("60000")            # A5
    delivery_commission_rate: Decimal = D("0.32")  # 平台抽成（作用在外送 gross）
    utilities: Decimal = D("17000")
    misc: Decimal = D("17000")            # 雜支/耗材/行銷
    dine_in_card_share: Decimal = D("0.5")   # 內用刷卡比例
    card_fee_rate: Decimal = D("0.025")      # 刷卡/行支手續費
    vat_rate: Decimal = D("0.05")            # 營業稅
    # 資本 / 折舊
    capex_depreciable: Decimal = D("1750000")  # 設備+裝潢（可折舊）
    depreciation_months: int = 60              # 5 年攤提
    capex_payback_base: Decimal = D("1750000") # 回本分子（CapEx，不含週轉金）


# ---------------------------------------------------------------------------
# 計算
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    name: str
    gmv: Decimal                  # 月營收（含稅）
    net_revenue_ex_vat: Decimal   # 未稅營收
    dine_in: Decimal
    delivery: Decimal
    daily_covers: Decimal
    # 成本
    cogs: Decimal
    labor: Decimal
    rent: Decimal
    commission: Decimal
    utilities: Decimal
    misc: Decimal
    card_fee: Decimal
    cash_costs: Decimal
    depreciation: Decimal
    vat_payable: Decimal
    # 損益
    cash_profit: Decimal          # 現金損益（不含折舊/稅）
    accounting_profit: Decimal    # 會計損益（含折舊、淨營業稅）
    labor_pct: Decimal
    rent_pct: Decimal
    # 損益兩平 / 回本
    fixed_cost: Decimal
    contribution_rate: Decimal
    breakeven_revenue: Decimal
    breakeven_daily: Decimal
    breakeven_covers: Decimal
    payback_months: Decimal | None  # None = 月損益 ≤ 0，無回本


def compute(a: Assumptions) -> Result:
    covers_value = D(a.seats) * a.turns_per_day * a.avg_ticket_incl_tax * D(a.days_per_month)
    gmv = covers_value
    dine_in = gmv * (D("1") - a.delivery_share)
    delivery = gmv * a.delivery_share
    daily_covers = D(a.seats) * a.turns_per_day

    net_revenue_ex_vat = gmv / (D("1") + a.vat_rate)

    cogs = gmv * a.food_cost_rate
    labor = a.fte * a.cost_per_fte
    commission = delivery * a.delivery_commission_rate
    card_fee = dine_in * a.dine_in_card_share * a.card_fee_rate
    cash_costs = cogs + labor + a.rent + commission + a.utilities + a.misc + card_fee

    cash_profit = gmv - cash_costs
    depreciation = a.capex_depreciable / D(a.depreciation_months)
    # 淨營業稅 ≈ (銷項 − 可扣抵進項) ；簡化為 (營收 − 食材) 的稅額（假設食材取得進項憑證）
    vat_payable = (gmv - cogs) * a.vat_rate / (D("1") + a.vat_rate)
    accounting_profit = cash_profit - depreciation - vat_payable

    # 損益兩平（準固定成本模型：小吃店人事視為準固定）
    fixed_cost = a.rent + labor + a.utilities + a.misc
    variable_rate = (
        a.food_cost_rate
        + a.delivery_share * a.delivery_commission_rate
        + (D("1") - a.delivery_share) * a.dine_in_card_share * a.card_fee_rate
    )
    contribution_rate = D("1") - variable_rate
    breakeven_revenue = fixed_cost / contribution_rate
    breakeven_daily = breakeven_revenue / D(a.days_per_month)
    breakeven_covers = breakeven_daily / a.avg_ticket_incl_tax

    payback = (a.capex_payback_base / cash_profit) if cash_profit > 0 else None

    return Result(
        name=a.name,
        gmv=_money(gmv),
        net_revenue_ex_vat=_money(net_revenue_ex_vat),
        dine_in=_money(dine_in),
        delivery=_money(delivery),
        daily_covers=daily_covers.quantize(D("0.1")),
        cogs=_money(cogs),
        labor=_money(labor),
        rent=_money(a.rent),
        commission=_money(commission),
        utilities=_money(a.utilities),
        misc=_money(a.misc),
        card_fee=_money(card_fee),
        cash_costs=_money(cash_costs),
        depreciation=_money(depreciation),
        vat_payable=_money(vat_payable),
        cash_profit=_money(cash_profit),
        accounting_profit=_money(accounting_profit),
        labor_pct=_pct(labor / gmv),
        rent_pct=_pct(a.rent / gmv),
        fixed_cost=_money(fixed_cost),
        contribution_rate=_pct(contribution_rate),
        breakeven_revenue=_money(breakeven_revenue),
        breakeven_daily=_money(breakeven_daily),
        breakeven_covers=breakeven_covers.quantize(D("1"), ROUND_HALF_UP),
        payback_months=(payback.quantize(D("0.1")) if payback is not None else None),
    )


def sensitivity(a: Assumptions) -> list[tuple[str, Decimal]]:
    """回傳各旋鈕對『現金損益』的影響（相對基準的 delta）。"""
    base = compute(a).cash_profit
    knobs = {
        "客單價 +10%": replace(a, avg_ticket_incl_tax=a.avg_ticket_incl_tax * D("1.1")),
        "翻桌率 +10%": replace(a, turns_per_day=a.turns_per_day * D("1.1")),
        "食材成本率 −3.5pp": replace(a, food_cost_rate=a.food_cost_rate - D("0.035")),
        "租金 −10%": replace(a, rent=a.rent * D("0.9")),
        "人事 −0.5 FTE": replace(a, fte=a.fte - D("0.5")),
    }
    out = []
    for label, mod in knobs.items():
        out.append((label, _money(compute(mod).cash_profit - base)))
    return out


# ---------------------------------------------------------------------------
# 報表
# ---------------------------------------------------------------------------


def _f(x: Decimal) -> str:
    return f"{x:,}"


def render(r: Result) -> str:
    verdict = "✅ 獲利" if r.cash_profit > 0 else "🚨 失血"
    lines = [
        f"── 情境：{r.name} ──",
        f"月營收(含稅 GMV)   {_f(r.gmv):>12}   (內用 {_f(r.dine_in)} / 外送 {_f(r.delivery)})",
        f"  未稅營收         {_f(r.net_revenue_ex_vat):>12}   日均來客 ≈ {r.daily_covers}",
        f"  食材 COGS        {_f(r.cogs):>12}",
        f"  人事             {_f(r.labor):>12}   占營收 {r.labor_pct}%",
        f"  租金             {_f(r.rent):>12}   占營收 {r.rent_pct}%",
        f"  外送平台抽成     {_f(r.commission):>12}",
        f"  水電/雜支        {_f(r.utilities + r.misc):>12}",
        f"  刷卡手續費       {_f(r.card_fee):>12}",
        f"  ── 現金成本      {_f(r.cash_costs):>12}",
        f"  ◆ 現金損益       {_f(r.cash_profit):>12}   {verdict}",
        f"  折舊攤提         {_f(r.depreciation):>12}",
        f"  淨營業稅(估)     {_f(r.vat_payable):>12}",
        f"  ◆ 會計損益(稅前) {_f(r.accounting_profit):>12}",
        f"損益兩平：月營收 {_f(r.breakeven_revenue)} / 日 {_f(r.breakeven_daily)} / "
        f"≈ {r.breakeven_covers} 客/日  (邊際貢獻率 {r.contribution_rate}%)",
        f"回本期：{(str(r.payback_months) + ' 個月') if r.payback_months else '月損益≤0,先止血,無回本可言'}",
    ]
    return "\n".join(lines)


# 三個內建情境（呼應 docs/19 §3）
PRESETS = [
    Assumptions(name="坐下型 翻桌2.0", turns_per_day=D("2.0")),
    Assumptions(name="快餐 翻桌3.0（藍圖 worked example）", turns_per_day=D("3.0")),
    Assumptions(
        name="修正可行版（客單220/翻桌3.5/人事3.5FTE/租50k）",
        turns_per_day=D("3.5"), avg_ticket_incl_tax=D("220"),
        fte=D("3.5"), rent=D("50000"),
    ),
]


def _build_override(args: argparse.Namespace) -> Assumptions:
    base = Assumptions(name="自訂")
    overrides: dict = {}
    for f in ("seats", "days_per_month", "depreciation_months"):
        v = getattr(args, f)
        if v is not None:
            overrides[f] = int(v)
    for f in (
        "turns_per_day", "avg_ticket_incl_tax", "delivery_share", "food_cost_rate",
        "fte", "cost_per_fte", "rent", "delivery_commission_rate", "utilities",
        "misc", "capex_depreciable", "capex_payback_base",
    ):
        v = getattr(args, f)
        if v is not None:
            overrides[f] = D(str(v))
    return replace(base, **overrides) if overrides else base


def main() -> None:
    p = argparse.ArgumentParser(description="開店單位經濟試算器（docs/19 §3）")
    p.add_argument("--json", action="store_true", help="機器可讀輸出")
    # 任一覆寫旗標出現 → 只跑自訂情境
    p.add_argument("--seats", type=int)
    p.add_argument("--turns", dest="turns_per_day")
    p.add_argument("--ticket", dest="avg_ticket_incl_tax")
    p.add_argument("--days", dest="days_per_month", type=int)
    p.add_argument("--delivery-share", dest="delivery_share")
    p.add_argument("--food-cost", dest="food_cost_rate")
    p.add_argument("--fte")
    p.add_argument("--cost-per-fte", dest="cost_per_fte")
    p.add_argument("--rent")
    p.add_argument("--commission", dest="delivery_commission_rate")
    p.add_argument("--utilities")
    p.add_argument("--misc")
    p.add_argument("--capex", dest="capex_depreciable")
    p.add_argument("--capex-payback", dest="capex_payback_base")
    p.add_argument("--depreciation-months", dest="depreciation_months", type=int)
    args = p.parse_args()

    has_override = any(
        getattr(args, f) is not None
        for f in (
            "seats", "turns_per_day", "avg_ticket_incl_tax", "days_per_month",
            "delivery_share", "food_cost_rate", "fte", "cost_per_fte", "rent",
            "delivery_commission_rate", "utilities", "misc", "capex_depreciable",
            "capex_payback_base", "depreciation_months",
        )
    )
    scenarios = [_build_override(args)] if has_override else PRESETS

    if args.json:
        out = [{k: (str(v) if isinstance(v, Decimal) else v)
                for k, v in asdict(compute(s)).items()} for s in scenarios]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    for s in scenarios:
        print(render(compute(s)))
        print()
    base = scenarios[1] if not has_override and len(scenarios) > 1 else scenarios[0]
    print(f"── 敏感度（對現金損益的影響 · 基準={base.name}）──")
    for label, delta in sensitivity(base):
        sign = "+" if delta >= 0 else ""
        print(f"  {label:<16} {sign}{_f(delta)}")
    print("\n注意：客單為含稅；COGS 以含稅 GMV 估算（精算用未稅營收）。數字僅供決策參考，"
          "落地以實際報價與當地法規為準。")


if __name__ == "__main__":
    main()
