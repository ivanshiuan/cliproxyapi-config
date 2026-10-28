"""截止日與缺件提醒 — 算出該提醒的事，組成一則 LINE 文字訊息。

只提醒可行動（符合 / 大致符合 / 有條件）的案件：兩週內截止的，
以及已開始申請但還缺文件的。每天自動推送屬於 Phase 5 的排程。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from restaurant_api.integrations.line import LineMessage, LineMessenger
from subsidy_radar.models import MatchResult, SubsidyApplication, SubsidyProgram, Verdict
from subsidy_radar.tracker import missing_documents

_ACTIONABLE = {Verdict.eligible, Verdict.likely, Verdict.conditional}
_LINE_TEXT_LIMIT = 5000


class Reminder(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_id: str
    program_name: str
    deadline: date | None
    days_left: int | None
    closes_soon: bool
    applying: bool
    missing: list[str] = Field(default_factory=list)


def build_reminders(
    programs: list[SubsidyProgram],
    matches: list[MatchResult],
    applications: dict[str, SubsidyApplication],
    today: date,
    horizon_days: int = 14,
) -> list[Reminder]:
    verdicts = {m.program_id: m.verdict for m in matches}
    reminders: list[Reminder] = []
    for program in programs:
        if verdicts.get(program.id) not in _ACTIONABLE:
            continue
        deadline = program.window_close
        days_left = (deadline - today).days if deadline else None
        closes_soon = days_left is not None and days_left <= horizon_days
        application = applications.get(program.id)
        missing = missing_documents(application) if application else []
        if closes_soon or missing:
            reminders.append(
                Reminder(
                    program_id=program.id,
                    program_name=program.name,
                    deadline=deadline,
                    days_left=days_left,
                    closes_soon=closes_soon,
                    applying=application is not None,
                    missing=missing,
                )
            )
    return sorted(
        reminders,
        key=lambda r: (r.days_left is None, r.days_left or 0, r.program_name),
    )


def describe_deadline(deadline: date | None, today: date) -> str:
    if deadline is None:
        return "常態受理"
    days_left = (deadline - today).days
    if days_left < 0:
        return f"{deadline:%m/%d} 已截止"
    return f"{deadline:%m/%d} 截止（剩 {days_left} 天）"


def _status_line(r: Reminder) -> str:
    if r.missing:
        return f"申請中，還缺 {len(r.missing)} 份：{'、'.join(r.missing)}"
    if r.applying:
        return "文件已備齊，記得在截止前送件"
    return "尚未開始申請"


def render_reminder_text(reminders: list[Reminder], today: date) -> str:
    lines = [f"🛰️ 補助提醒 {today}"]
    for r in reminders:
        lines.append("")
        lines.append(f"{'⏰' if r.closes_soon else '📎'} {r.program_name}")
        lines.append(f"{describe_deadline(r.deadline, today)}｜{_status_line(r)}")
    text = "\n".join(lines)
    if len(text) > _LINE_TEXT_LIMIT:
        return text[: _LINE_TEXT_LIMIT - 1] + "…"
    return text


async def push_reminders(messenger: LineMessenger, to: str, text: str) -> None:
    await messenger.push(to, LineMessage(kind="text", text=text))
