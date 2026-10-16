"""Server-rendered page for the daily P&L (``/reports/daily-pnl?format=html``).

One self-contained document: no external assets, no JavaScript, readable on a phone. Every dynamic
string goes through ``html.escape`` (fixed-cost names come from configuration, not from code).
User-facing strings use ASCII punctuation, matching the rest of ``restaurant_api``.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from ..schemas.daily_pnl import DailyPnlReport, PnlLine

_WEEKDAYS = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")

_CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1c1a;--mute:#6b6963;--line:#e4e2db;--good:#1b7a3d;--bad:#b3261e;--warn:#8a5a00;--warn-bg:#fff4dc}
@media(prefers-color-scheme:dark){:root{--bg:#141412;--card:#1e1e1b;--ink:#efede8;--mute:#a09d94;--line:#34332e;--good:#58c47c;--bad:#f2766d;--warn:#f0c060;--warn-bg:#2d2512}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:20px;margin:0}
h2{font-size:15px;margin:0 0 8px;color:var(--mute);font-weight:600;letter-spacing:.04em}
.sub{color:var(--mute);margin:2px 0 16px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.hero{text-align:center;padding:22px 16px}
.hero .label{color:var(--mute);font-size:13px}
.hero .big{font-size:40px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.pos .big{color:var(--good)}.neg .big{color:var(--bad)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td{padding:6px 0;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
td.n{text-align:right;white-space:nowrap}
tr.total td{font-weight:700;border-top:2px solid var(--line)}
tr.sub td{color:var(--mute);font-size:13px;padding-left:14px}
.neg-n{color:var(--bad)}
.warn{background:var(--warn-bg);border-color:transparent}.warn h2,.warn li{color:var(--warn)}
ul{margin:0;padding-left:20px}li{margin:4px 0}
.ok{color:var(--good)}.bad{color:var(--bad)}
.foot{color:var(--mute);font-size:12px;text-align:center;margin-top:20px}
"""


def money(v: Decimal, *, places: int = 2) -> str:
    q = Decimal(1).scaleb(-places)
    return f"{v.quantize(q, rounding=ROUND_HALF_UP):,.{places}f}"


def _signed(v: Decimal) -> str:
    cls = "neg-n" if v < 0 else ""
    return f'<span class="{cls}">{escape(money(v))}</span>'


def _row(label: str, value: Decimal, *, cls: str = "", sign: bool = False, note: str | None = None) -> str:
    shown = _signed(value) if sign else escape(money(value))
    extra = f'<div class="mute">{escape(note)}</div>' if note else ""
    return f'<tr class="{cls}"><td>{escape(label)}{extra}</td><td class="n">{shown}</td></tr>'


def _lines(lines: list[PnlLine]) -> str:
    return "".join(_row(ln.label, ln.amount, cls="sub", note=ln.note) for ln in lines)


def render_html(r: DailyPnlReport) -> str:
    d: date = r.business_date
    positive = r.net_profit >= 0
    margin = "" if r.net_margin is None else f" · 淨利率 {money(r.net_margin * 100, places=1)}%"
    recon_ok = abs(r.payments_diff) < Decimal("0.01")
    recon = (
        '<span class="ok">帳實相符</span>'
        if recon_ok
        else f'<span class="bad">差 {escape(money(r.payments_diff))}</span>'
    )

    revenue = (
        _row("菜單原價(含稅)", r.menu_gross)
        + _row("折扣", -r.discount_total, sign=True)
        + _row("服務費", r.service_charge)
        + _row("客人應付(含稅)", r.billed_incl_vat, cls="total")
        + _row("營業稅(不算營收)", -r.vat, sign=True)
        + _row("營收(未稅)", r.revenue_ex_vat, cls="total")
    )
    costs = (
        _row("食材成本(賣出的餐點)", -r.cogs, sign=True)
        + _row("報廢", -r.cost_waste, sign=True)
        + _row("員工餐", -r.cost_staff_meal, sign=True)
        + _row("試菜", -r.cost_tasting, sign=True)
        + _row("收款手續費", -r.payment_fees, sign=True)
        + _row("毛利", r.gross_profit, cls="total", sign=True)
        + _row("人事成本", -r.labor_cost, sign=True)
        + _lines(r.labor_lines)
        + _row("固定成本", -r.fixed_cost_total, sign=True)
        + _lines(r.fixed_cost_lines)
        + _row("淨利(估算)", r.net_profit, cls="total", sign=True)
    )
    warnings = (
        '<section class="warn"><h2>請注意: 這些會讓數字失真</h2><ul>'
        + "".join(f"<li>{escape(w)}</li>" for w in r.warnings)
        + "</ul></section>"
        if r.warnings
        else ""
    )
    assumptions = "".join(f"<li>{escape(a)}</li>" for a in r.assumptions)
    orders = f"{r.order_count} 張已結帳" + (f" · {r.open_order_count} 張未結帳" if r.open_order_count else "")

    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>每日損益 {escape(r.store_name)} {d.isoformat()}</title>
<style>{_CSS}</style></head><body><main>
<h1>{escape(r.store_name)}</h1>
<p class="sub">{d.isoformat()} {_WEEKDAYS[d.weekday()]} · 每日損益(估算) · {escape(orders)}</p>
<section class="hero {"pos" if positive else "neg"}">
<div class="label">今日估算淨利(未稅)</div>
<div class="big">NT$ {escape(money(r.net_profit, places=0))}</div>
<div class="label">營收(未稅) NT$ {escape(money(r.revenue_ex_vat, places=0))}{escape(margin)}</div>
</section>
<section><h2>營收</h2><table>{revenue}</table></section>
<section><h2>成本與淨利</h2><table>{costs}</table></section>
<section><h2>對帳</h2>
<table>{_row("收款合計", r.payments_total)}{_row("客人應付(含稅)", r.billed_incl_vat)}
<tr><td>結果</td><td class="n">{recon}</td></tr></table></section>
{warnings}
<section><h2>這份數字怎麼算的</h2><ul>{assumptions}</ul></section>
<p class="foot">估算值, 不是會計師簽證的財報。金額單位 NT$。</p>
</main></body></html>"""
