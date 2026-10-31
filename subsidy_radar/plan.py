"""行動包產生器 — 每一案一包「照著做就能送件」。

generate_plans() 吃 MatchResult + SubsidyProgram，只為
verdict = eligible / likely / conditional 的案件產包。
兩週內截止的案件排最前，其餘依分數排序。
"""

from __future__ import annotations

from datetime import date

from subsidy_radar.models import (
    ActionPlan,
    ActionStep,
    MatchResult,
    SubsidyProgram,
    Verdict,
    taipei_today,
)

_ACTIONABLE = {Verdict.eligible, Verdict.likely, Verdict.conditional}
_URGENT_DAYS = 14


def _closes_soon(deadline: date | None, today: date) -> bool:
    return deadline is not None and (deadline - today).days <= _URGENT_DAYS


def _urgency(deadline: date | None, today: date) -> str:
    if deadline is None:
        return "🟢 常態受理"
    days_left = (deadline - today).days
    if days_left <= _URGENT_DAYS:
        return f"🔴 急（剩 {days_left} 天）"
    if days_left <= 45:
        return f"🟠 中（剩 {days_left} 天）"
    return f"🟢 充裕（剩 {days_left} 天）"


def _draft_notes(match: MatchResult) -> str | None:
    lines: list[str] = []
    if match.recommended_angle:
        lines.append(f"建議切角：{match.recommended_angle}")
    if match.strategic_notes:
        lines.append(f"申請策略：{match.strategic_notes}")
    return "\n".join(lines) or None


def _base_steps(program: SubsidyProgram, match: MatchResult) -> list[ActionStep]:
    steps: list[ActionStep] = []
    order = 0

    if match.uncertainties:
        order += 1
        steps.append(
            ActionStep(
                order=order,
                title="確認資格待確認項目",
                detail="以下事項需先釐清：\n" + "\n".join(f"  - {u}" for u in match.uncertainties),
                owner="Ivan",
            )
        )

    order += 1
    steps.append(
        ActionStep(
            order=order,
            title="查閱官方公告原文",
            detail=program.apply_url or "（見 source_urls）",
            owner="Ivan",
        )
    )

    if program.required_documents:
        order += 1
        steps.append(
            ActionStep(
                order=order,
                title="準備應備文件",
                detail="\n".join(f"  - {d}" for d in program.required_documents)
                + f"\n  （`python -m subsidy_radar apply {program.id}` 開始追蹤備齊進度）",
                owner="Ivan",
            )
        )

    if program.how_to_apply:
        order += 1
        steps.append(
            ActionStep(
                order=order,
                title="送出申請",
                detail=program.how_to_apply,
                owner="Ivan",
            )
        )

    order += 1
    steps.append(
        ActionStep(
            order=order,
            title="Claude 代擬申請書草稿",
            detail=(
                f"`python -m subsidy_radar draft {program.id}`"
                "（需 ANTHROPIC_API_KEY；草稿需人工覆核後再送出）"
            ),
            owner="Claude",
        )
    )

    return steps


def generate_plan(
    program: SubsidyProgram,
    match: MatchResult,
    priority: int,
    today: date | None = None,
) -> ActionPlan:
    today = today or taipei_today()
    return ActionPlan(
        program_id=program.id,
        program_name=program.name,
        priority=priority,
        deadline=program.window_close,
        urgency=_urgency(program.window_close, today),
        steps=_base_steps(program, match),
        required_documents=program.required_documents,
        notes=program.notes,
        draft_notes=_draft_notes(match),
        disclaimer=(
            "此行動包由 AI 依公開資訊產出，僅供參考。"
            "正式送件前請逐項核對官方最新公告，並由負責人簽名確認。"
        ),
    )


def generate_plans(
    programs: list[SubsidyProgram],
    matches: list[MatchResult],
    today: date | None = None,
) -> list[ActionPlan]:
    now = today or taipei_today()
    prog_map = {p.id: p for p in programs}
    pairs = [
        (prog_map[m.program_id], m)
        for m in matches
        if m.verdict in _ACTIONABLE and m.program_id in prog_map
    ]
    pairs.sort(key=lambda pm: (not _closes_soon(pm[0].window_close, now), -pm[1].score))
    return [generate_plan(p, m, priority=i, today=now) for i, (p, m) in enumerate(pairs, 1)]
