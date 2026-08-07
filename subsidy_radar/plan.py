"""行動包產生器 — 每一案一包「照著做就能送件」。

generate_plans() 吃 MatchResult + SubsidyProgram，只為
verdict = eligible / likely / conditional 的案件產包。
"""

from __future__ import annotations

from datetime import date, timedelta

from subsidy_radar.models import (
    ActionPlan,
    ActionStep,
    MatchResult,
    SubsidyProgram,
    Verdict,
)

_ACTIONABLE = {Verdict.eligible, Verdict.likely, Verdict.conditional}


def _urgency(deadline: date | None, today: date) -> str:
    if deadline is None:
        return "🟢 常態受理"
    days_left = (deadline - today).days
    if days_left <= 14:
        return "🔴 急（不到兩週）"
    if days_left <= 45:
        return "🟠 中（一個半月內）"
    return "🟢 充裕"


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
                detail="\n".join(f"  - {d}" for d in program.required_documents),
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
            detail="用 `subsidy draft <program_id>` 產出草稿（需人工覆核後再送出）",
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
    today = today or date.today()
    deadline = program.window_close
    internal_deadline = None
    if deadline:
        internal_deadline = deadline - timedelta(days=7)

    return ActionPlan(
        program_id=program.id,
        program_name=program.name,
        priority=priority,
        deadline=internal_deadline or deadline,
        urgency=_urgency(deadline, today),
        steps=_base_steps(program, match),
        required_documents=program.required_documents,
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
    prog_map = {p.id: p for p in programs}
    actionable = [m for m in matches if m.verdict in _ACTIONABLE]
    actionable.sort(key=lambda m: -m.score)

    plans: list[ActionPlan] = []
    for i, m in enumerate(actionable, 1):
        prog = prog_map.get(m.program_id)
        if prog is None:
            continue
        plans.append(generate_plan(prog, m, priority=i, today=today))
    return plans
