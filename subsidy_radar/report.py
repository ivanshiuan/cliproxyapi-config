"""Markdown 報告渲染器。

RadarReport → docs/subsidies/RADAR_2026.md

三大區塊：
1. 總覽表（所有案件 × 判定結果 × 預估金額）
2. 可行動案件的行動包
3. 不符合 / 追蹤中案件簡表
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from subsidy_radar.models import (
    ActionPlan,
    MatchResult,
    RadarReport,
    Verdict,
)

TAIPEI = ZoneInfo("Asia/Taipei")

_VERDICT_LABEL = {
    Verdict.eligible: "✅ 符合",
    Verdict.likely: "🟡 大致符合",
    Verdict.conditional: "🟠 有條件",
    Verdict.watch: "👀 追蹤",
    Verdict.ineligible: "❌ 不符",
    Verdict.unknown: "❓ 未知",
}

_VERDICT_ORDER = {
    Verdict.eligible: 0,
    Verdict.likely: 1,
    Verdict.conditional: 2,
    Verdict.watch: 3,
    Verdict.unknown: 4,
    Verdict.ineligible: 5,
}


def _fmt_amount(amt: object) -> str:
    if amt is None:
        return "-"
    try:
        from decimal import Decimal

        v = Decimal(str(amt))
        if v >= 10000:
            wan = v / 10000
            return f"{wan:,.1f} 萬"
        return f"{v:,}"
    except Exception:
        return str(amt)


def render_summary_table(matches: list[MatchResult]) -> str:
    rows = sorted(matches, key=lambda m: (_VERDICT_ORDER.get(m.verdict, 9), -m.score))
    lines = [
        "| # | 補助案 | 判定 | 分數 | 預估金額 | 說明 |",
        "|---|--------|------|------|----------|------|",
    ]
    for i, m in enumerate(rows, 1):
        label = _VERDICT_LABEL.get(m.verdict, str(m.verdict))
        amt = _fmt_amount(m.estimated_value_twd)
        reason = m.reasoning[:60] if m.reasoning else ""
        lines.append(f"| {i} | {m.program_name} | {label} | {m.score} | {amt} | {reason} |")
    return "\n".join(lines)


def render_action_plan(plan: ActionPlan) -> str:
    lines = [
        f"### P{plan.priority}：{plan.program_name}",
        "",
        f"- 截止日：{plan.deadline or '常態受理'}",
        f"- 緊急度：{plan.urgency}",
        "",
    ]
    if plan.steps:
        lines.append("**步驟：**\n")
        for s in plan.steps:
            lines.append(f"{s.order}. **{s.title}**（{s.owner}）")
            if s.detail:
                for detail_line in s.detail.split("\n"):
                    lines.append(f"   {detail_line}")
            lines.append("")
    if plan.required_documents:
        lines.append("**應備文件：**\n")
        for d in plan.required_documents:
            lines.append(f"- {d}")
        lines.append("")
    if plan.disclaimer:
        lines.append(f"> ⚠️ {plan.disclaimer}")
        lines.append("")
    return "\n".join(lines)


def render_report(report: RadarReport) -> str:
    ts = report.generated_at.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M %Z")
    p = report.profile
    eligible_value = sum(
        m.estimated_value_twd
        for m in report.matches
        if m.verdict in {Verdict.eligible, Verdict.likely} and m.estimated_value_twd
    )

    sections = [
        "# 🛰️ 2026 政府補助雷達報告",
        "",
        f"> 產出時間：{ts}",
        f"> 公司：{p.company_name}｜行業：{p.industry_desc}（{p.industry_code}）",
        f"> 員工數：{p.employee_count}｜資本額：{_fmt_amount(p.capital_twd)}",
        "",
    ]

    actionable_count = sum(
        1 for m in report.matches if m.verdict in {Verdict.eligible, Verdict.likely, Verdict.conditional}
    )
    sections.append(
        f"**掃描 {len(report.programs)} 項補助案 → "
        f"{actionable_count} 項可行動 → "
        f"預估可爭取 NT$ {_fmt_amount(eligible_value)}**"
    )
    sections.append("")

    if p.assumptions:
        sections.append("⚠️ **以下資料為推測值，送件前務必確認：**")
        for a in p.assumptions:
            sections.append(f"- {a}")
        sections.append("")

    sections.append("---")
    sections.append("")
    sections.append("## 一覽表")
    sections.append("")
    sections.append(render_summary_table(report.matches))
    sections.append("")

    if report.plans:
        sections.append("---")
        sections.append("")
        sections.append("## 行動包")
        sections.append("")
        for plan in report.plans:
            sections.append(render_action_plan(plan))
            sections.append("")

    watch = [m for m in report.matches if m.verdict == Verdict.watch]
    if watch:
        sections.append("---")
        sections.append("")
        sections.append("## 追蹤中（本輪已截止，關注下一輪）")
        sections.append("")
        for m in watch:
            sections.append(f"- **{m.program_name}**：{m.reasoning}")

    ineligible = [m for m in report.matches if m.verdict == Verdict.ineligible]
    if ineligible:
        sections.append("")
        sections.append("## 不符合")
        sections.append("")
        for m in ineligible:
            fails = "；".join(m.hard_fails) if m.hard_fails else m.reasoning
            sections.append(f"- **{m.program_name}**：{fails}")

    sections.append("")
    sections.append("---")
    sections.append("*此報告由 subsidy_radar 自動產出，僅供內部參考。")
    sections.append("正式申請前請以各主辦機關官方公告為準。*")
    sections.append("")
    return "\n".join(sections)


def generate_report(report: RadarReport) -> str:
    return render_report(report)
