"""每日監控 — 截止倒數、審查追蹤、新公告偵測，組成一則每日摘要。

只提醒「今天剛好碰到」的事，讓每天自動推送不洗版：
- 截止倒數只在 D-30、D-14、D-7 與最後三天，送件後就停；
- 送件滿 30／60／90 天還沒結果，提醒追蹤審查；
- 來源頁出現「新連結且標題含補助關鍵字」才算新公告。第一次抓某來源只建基準、不提醒。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from subsidy_radar.extract import html_anchors
from subsidy_radar.fetch import fetch_all
from subsidy_radar.models import (
    MatchResult,
    Stage,
    SubsidyApplication,
    SubsidyProgram,
    Verdict,
)
from subsidy_radar.remind import describe_deadline
from subsidy_radar.tracker import missing_documents

COUNTDOWN_DAYS = frozenset({30, 14, 7, 3, 2, 1, 0})
FOLLOW_UP_DAYS = frozenset({30, 60, 90})
ANNOUNCEMENT_KEYWORDS = ("補助", "補貼", "獎勵", "獎助", "受理", "徵件", "徵選", "貸款", "計畫")
MAX_ANNOUNCEMENTS_PER_SOURCE = 5

_ACTIONABLE = {Verdict.eligible, Verdict.likely, Verdict.conditional}
_LINE_TEXT_LIMIT = 5000


class DailyAlert(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["deadline", "follow_up", "announcement"]
    title: str
    detail: str
    url: str | None = None


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    url: str
    inbox_only: bool = False


# ── 截止倒數與審查追蹤 ────────────────────────────────────────────────────


def countdown_alerts(
    programs: list[SubsidyProgram],
    matches: list[MatchResult],
    applications: dict[str, SubsidyApplication],
    today: date,
) -> list[DailyAlert]:
    verdicts = {m.program_id: m.verdict for m in matches}
    due: list[tuple[int, DailyAlert]] = []
    for program in programs:
        if verdicts.get(program.id) not in _ACTIONABLE or program.window_close is None:
            continue
        days_left = (program.window_close - today).days
        application = applications.get(program.id)
        submitted = application is not None and application.stage != Stage.preparing
        if days_left not in COUNTDOWN_DAYS or submitted:
            continue
        missing = missing_documents(application) if application else []
        if missing:
            status = f"還缺 {len(missing)} 份：{'、'.join(missing)}"
        elif application:
            status = "文件已備齊，記得送件"
        else:
            status = "尚未開始申請"
        detail = f"{describe_deadline(program.window_close, today)}｜{status}"
        due.append(
            (days_left, DailyAlert(kind="deadline", title=program.name, detail=detail, url=program.apply_url))
        )
    return [alert for _, alert in sorted(due, key=lambda item: (item[0], item[1].title))]


def follow_up_alerts(
    programs: list[SubsidyProgram],
    applications: dict[str, SubsidyApplication],
    today: date,
) -> list[DailyAlert]:
    names = {p.id: p.name for p in programs}
    alerts: list[DailyAlert] = []
    for program_id, application in applications.items():
        if application.stage not in (Stage.submitted, Stage.reviewing):
            continue
        if application.submitted_on is None:
            continue
        days = (today - application.submitted_on).days
        if days in FOLLOW_UP_DAYS:
            alerts.append(
                DailyAlert(
                    kind="follow_up",
                    title=names.get(program_id, program_id),
                    detail=f"送件已 {days} 天還沒有結果，建議聯絡主辦機關追蹤審查進度",
                )
            )
    return alerts


# ── 新公告偵測 ────────────────────────────────────────────────────────────


def load_sources(path: Path) -> list[Source]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Source.model_validate(item) for item in raw.get("sources") or []]


def load_state(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def detect_announcements(
    source: Source, html: str, seen: set[str] | None
) -> tuple[list[DailyAlert], set[str]]:
    """回傳（新公告提醒, 更新後已看過的網址）。seen 為 None 表示第一次抓，只建基準。"""
    anchors = html_anchors(html, source.url)
    urls = {url for _, url in anchors}
    if seen is None:
        return [], urls
    alerts: list[DailyAlert] = []
    reported: set[str] = set()
    for text, url in anchors:
        if url in seen or url in reported:
            continue
        if not any(keyword in text for keyword in ANNOUNCEMENT_KEYWORDS):
            continue
        reported.add(url)
        alerts.append(
            DailyAlert(kind="announcement", title=text, detail=f"{source.name} 新公告", url=url)
        )
    return alerts[:MAX_ANNOUNCEMENTS_PER_SOURCE], seen | urls


async def scan_sources(
    sources: list[Source], state: dict[str, list[str]], raw_dir: Path
) -> tuple[list[DailyAlert], dict[str, list[str]], list[str]]:
    """抓所有可抓的來源找新公告；回傳（提醒, 新狀態, 失敗說明）。單一來源失敗不影響其他。"""
    targets = [s for s in sources if not s.inbox_only]
    outcomes = await fetch_all([(s.id, s.url) for s in targets], raw_dir)
    alerts: list[DailyAlert] = []
    failures: list[str] = []
    new_state = dict(state)
    for source, outcome in zip(targets, outcomes, strict=True):
        if not outcome.ok or outcome.snapshot_path is None:
            failures.append(f"{source.name}：{outcome.error}")
            continue
        html = Path(outcome.snapshot_path).read_text(encoding="utf-8")
        seen = set(state[source.id]) if source.id in state else None
        found, seen_now = detect_announcements(source, html, seen)
        alerts.extend(found)
        new_state[source.id] = sorted(seen_now)
    return alerts, new_state, failures


# ── 摘要 ──────────────────────────────────────────────────────────────────

_SECTIONS = (
    ("deadline", "⏰ 截止倒數"),
    ("follow_up", "📮 審查追蹤"),
    ("announcement", "📰 新公告"),
)


def render_daily(alerts: list[DailyAlert], today: date) -> str:
    lines = [f"🛰️ 補助每日摘要 {today}"]
    for kind, heading in _SECTIONS:
        group = [a for a in alerts if a.kind == kind]
        if not group:
            continue
        lines += ["", heading]
        for alert in group:
            lines.append(f"・{alert.title}")
            lines.append(f"  {alert.detail}")
            if alert.url and kind == "announcement":
                lines.append(f"  {alert.url}")
    text = "\n".join(lines)
    if len(text) > _LINE_TEXT_LIMIT:
        return text[: _LINE_TEXT_LIMIT - 1] + "…"
    return text
