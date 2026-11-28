"""subsidy_radar Phase 5 — 申請階段、每日摘要、新公告偵測。不打網路、不打 API、不需 DB。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from restaurant_api.integrations.line import HttpLineMessenger
from subsidy_radar.cli import app
from subsidy_radar.extract import html_anchors
from subsidy_radar.fetch import FetchOutcome
from subsidy_radar.models import (
    CompanyProfile,
    DataConfidence,
    MatchResult,
    ProgramCategory,
    ProgramStatus,
    RadarReport,
    Stage,
    SubsidyProgram,
    Verdict,
    taipei_today,
)
from subsidy_radar.monitor import (
    Source,
    countdown_alerts,
    detect_announcements,
    follow_up_alerts,
    render_daily,
    scan_sources,
)
from subsidy_radar.remind import build_reminders
from subsidy_radar.report import generate_report
from subsidy_radar.tracker import (
    load_applications,
    save_applications,
    set_stage,
    start_application,
)

PROFILE_PATH = Path("data/subsidies/company_profile.yaml")
TODAY = date(2026, 10, 29)
SOURCE = Source(id="gov", name="某部會", url="https://example.gov.tw/news/index.aspx")
runner = CliRunner()


def _program(**overrides: Any) -> SubsidyProgram:
    fields: dict[str, Any] = {
        "id": "p",
        "name": "Program",
        "agency": "Agency",
        "category": ProgramCategory.grant,
        "status": ProgramStatus.open,
        "required_documents": ["計畫書", "公司登記證明"],
        "as_of": date(2026, 10, 1),
        "confidence": DataConfidence.manual,
    }
    fields.update(overrides)
    return SubsidyProgram(**fields)


def _match(program_id: str, verdict: Verdict = Verdict.likely) -> MatchResult:
    return MatchResult(program_id=program_id, program_name=program_id, verdict=verdict, score=50)


def _page(*links: tuple[str, str]) -> str:
    anchors = "".join(f'<li><a href="{href}">{text}</a></li>' for text, href in links)
    return f"<html><body><ul>{anchors}</ul></body></html>"


# ── 申請階段 ──────────────────────────────────────────────────────────────


class TestStage:
    def test_submitted_records_date(self) -> None:
        application = set_stage(start_application(_program(), today=TODAY), Stage.submitted, TODAY)
        assert application.stage == Stage.submitted
        assert application.submitted_on == TODAY
        assert application.decided_on is None

    def test_approved_records_decision_and_amount(self) -> None:
        application = start_application(_program(), today=TODAY)
        application = set_stage(application, Stage.approved, TODAY, "1,500,000")
        assert application.decided_on == TODAY
        assert application.awarded_twd == Decimal("1500000")

    def test_amount_rejects_float(self) -> None:
        with pytest.raises(ValidationError):
            set_stage(start_application(_program(), today=TODAY), Stage.approved, TODAY, 1.5)  # type: ignore[arg-type]

    def test_yaml_roundtrip_keeps_stage(self, tmp_path: Path) -> None:
        path = tmp_path / "applications.yaml"
        application = set_stage(start_application(_program(), today=TODAY), Stage.approved, TODAY, "50000")
        save_applications(path, {"p": application})
        assert load_applications(path) == {"p": application}


# ── 截止倒數、審查追蹤 ────────────────────────────────────────────────────


class TestDailyAlerts:
    def test_countdown_only_on_milestones(self) -> None:
        programs = [
            _program(id=f"d{days}", name=f"D-{days}", window_close=TODAY + timedelta(days=days))
            for days in (30, 8, 7, 0)
        ]
        alerts = countdown_alerts(programs, [_match(p.id) for p in programs], {}, TODAY)
        assert [a.title for a in alerts] == ["D-0", "D-7", "D-30"]
        assert all("尚未開始申請" in a.detail for a in alerts)

    def test_countdown_skips_submitted_and_inactionable(self) -> None:
        tracked = _program(id="tracked", name="Tracked", window_close=TODAY + timedelta(days=7))
        sent = _program(id="sent", name="Sent", window_close=TODAY + timedelta(days=7))
        watch = _program(id="watch", name="Watch", window_close=TODAY + timedelta(days=7))
        applications = {
            "tracked": start_application(tracked, today=TODAY),
            "sent": set_stage(start_application(sent, today=TODAY), Stage.submitted, TODAY),
        }
        matches = [_match("tracked"), _match("sent"), _match("watch", Verdict.watch)]
        alerts = countdown_alerts([tracked, sent, watch], matches, applications, TODAY)
        assert [a.title for a in alerts] == ["Tracked"]
        assert "還缺 2 份" in alerts[0].detail

    def test_follow_up_after_30_60_90_days(self) -> None:
        program = _program()
        applications = {
            f"p{days}": set_stage(
                start_application(_program(id=f"p{days}"), today=TODAY),
                Stage.submitted,
                TODAY - timedelta(days=days),
            )
            for days in (30, 31, 60)
        }
        applications["done"] = set_stage(
            set_stage(start_application(program, today=TODAY), Stage.submitted, TODAY - timedelta(days=30)),
            Stage.approved,
            TODAY,
        )
        alerts = follow_up_alerts([program], applications, TODAY)
        assert sorted(a.title for a in alerts) == ["p30", "p60"]

    def test_remind_skips_submitted_applications(self) -> None:
        program = _program(window_close=TODAY + timedelta(days=3))
        submitted = set_stage(start_application(program, today=TODAY), Stage.submitted, TODAY)
        assert build_reminders([program], [_match("p")], {"p": submitted}, TODAY) == []

    def test_render_daily_groups_by_kind(self) -> None:
        programs = [_program(name="小人提", window_close=TODAY + timedelta(days=1))]
        alerts = countdown_alerts(programs, [_match("p")], {}, TODAY)
        found, _ = detect_announcements(
            SOURCE, _page(("116年度補助受理", "/n/1")), seen=set()
        )
        text = render_daily(alerts + found, TODAY)
        assert text.index("截止倒數") < text.index("新公告")
        assert "剩 1 天" in text
        assert "https://example.gov.tw/n/1" in text


# ── 新公告偵測 ────────────────────────────────────────────────────────────


class TestAnnouncements:
    def test_html_anchors_resolves_links(self) -> None:
        html = (
            '<a href="/news?id=1">116年度 補助\n受理</a>'
            '<a href="https://x.gov.tw/p#top" title="計畫說明"><img src="i.png"></a>'
            '<a href="javascript:void(0)">展開</a><a href="mailto:a@b.tw">信箱</a>'
        )
        assert html_anchors(html, "https://x.gov.tw/index.aspx") == [
            ("116年度 補助 受理", "https://x.gov.tw/news?id=1"),
            ("計畫說明", "https://x.gov.tw/p"),
        ]

    def test_first_scan_only_records_baseline(self) -> None:
        alerts, seen = detect_announcements(SOURCE, _page(("115年度補助計畫", "/n/1")), seen=None)
        assert alerts == []
        assert seen == {"https://example.gov.tw/n/1"}

    def test_only_new_links_with_keywords_alert(self) -> None:
        baseline = {"https://example.gov.tw/n/1"}
        html = _page(("115年度補助計畫", "/n/1"), ("116年度數位轉型補助受理申請", "/n/2"), ("聯絡我們", "/contact"))
        alerts, seen = detect_announcements(SOURCE, html, seen=baseline)
        assert [(a.title, a.url) for a in alerts] == [
            ("116年度數位轉型補助受理申請", "https://example.gov.tw/n/2")
        ]
        assert "https://example.gov.tw/contact" in seen

    def test_alerts_capped_per_source(self) -> None:
        html = _page(*[(f"第{i}梯次補助公告", f"/n/{i}") for i in range(8)])
        alerts, _ = detect_announcements(SOURCE, html, seen=set())
        assert len(alerts) == 5

    async def test_scan_survives_fetch_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        snapshot = tmp_path / "ok.html"
        snapshot.write_text(_page(("116年度補助受理", "/n/9")), encoding="utf-8")

        async def fake_fetch_all(targets: list[tuple[str, str]], raw_dir: Path) -> list[FetchOutcome]:
            assert [sid for sid, _ in targets] == ["down", "gov"]
            return [
                FetchOutcome(source_id="down", url="https://down.gov.tw", ok=False, error="HTTP 403"),
                FetchOutcome(source_id="gov", url=SOURCE.url, ok=True, snapshot_path=str(snapshot)),
            ]

        monkeypatch.setattr("subsidy_radar.monitor.fetch_all", fake_fetch_all)
        sources = [
            Source(id="down", name="Down", url="https://down.gov.tw"),
            SOURCE,
            Source(id="inbox", name="Inbox", url="https://inbox.example", inbox_only=True),
        ]
        alerts, state, failures = await scan_sources(sources, {"gov": []}, tmp_path)
        assert [a.url for a in alerts] == ["https://example.gov.tw/n/9"]
        assert state["gov"] == ["https://example.gov.tw/n/9"]
        assert len(failures) == 1
        assert failures[0].startswith("Down")
        assert failures[0].endswith("HTTP 403")


# ── 報告 ──────────────────────────────────────────────────────────────────


def test_report_shows_application_progress(profile_for_report: CompanyProfile) -> None:
    program = _program(name="小人提")
    application = set_stage(start_application(program, today=TODAY), Stage.approved, TODAY, "50000")
    report = RadarReport(
        generated_at=datetime(2026, 10, 29, tzinfo=UTC),
        profile=profile_for_report,
        programs=[program],
        applications={"p": application},
    )
    md = generate_report(report)
    assert "## 申請進度" in md
    assert "已核准 1 案" in md
    assert "5.0 萬" in md


@pytest.fixture
def profile_for_report() -> CompanyProfile:
    return CompanyProfile(
        company_name="Test Restaurant",
        industry_code="5610",
        industry_desc="Restaurants",
        employee_count=15,
        capital_twd=Decimal("5000000"),
        founded=date(2023, 6, 1),
        region="台北市",
    )


# ── CLI ───────────────────────────────────────────────────────────────────


def _seed_closing_in(tmp_path: Path, days: int) -> str:
    """One open program closing in `days` days, with no gates, so the real profile keeps it actionable."""
    seed = tmp_path / f"seed_{days}.json"
    closes = taipei_today() + timedelta(days=days)
    program = _program(id="soon", name="Soon Grant", window_close=closes)
    seed.write_text(json.dumps([program.model_dump(mode="json")], ensure_ascii=False), encoding="utf-8")
    return str(seed)


@pytest.fixture
def line_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    messenger = HttpLineMessenger(channel_access_token="t", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("subsidy_radar.cli.HttpLineMessenger.from_env", lambda: messenger)
    return requests


class TestCli:
    def test_stage_flow(self, tmp_path: Path) -> None:
        tracker = str(tmp_path / "applications.yaml")
        assert runner.invoke(app, ["apply", "sbir-phase1", "--tracker", tracker]).exit_code == 0
        result = runner.invoke(
            app, ["stage", "sbir-phase1", "submitted", "--on", "2026-10-01", "--tracker", tracker]
        )
        assert result.exit_code == 0, result.output
        result = runner.invoke(
            app, ["stage", "sbir-phase1", "approved", "--amount", "300000", "--tracker", tracker]
        )
        assert result.exit_code == 0, result.output
        application = load_applications(Path(tracker))["sbir-phase1"]
        assert application.submitted_on == date(2026, 10, 1)
        assert application.awarded_twd == Decimal("300000")
        assert "核准" in runner.invoke(app, ["status", "--tracker", tracker]).output

    def test_stage_rejects_bad_input(self, tmp_path: Path) -> None:
        tracker = str(tmp_path / "applications.yaml")
        assert runner.invoke(app, ["stage", "sbir-phase1", "submitted", "--tracker", tracker]).exit_code == 1
        runner.invoke(app, ["apply", "sbir-phase1", "--tracker", tracker])
        args = ["stage", "sbir-phase1", "rejected", "--amount", "5", "--tracker", tracker]
        assert runner.invoke(app, args).exit_code == 1
        args = ["stage", "sbir-phase1", "submitted", "--on", "10/30", "--tracker", tracker]
        assert runner.invoke(app, args).exit_code == 1

    def test_daily_pushes_only_when_something_is_due(
        self, tmp_path: Path, line_requests: list[httpx.Request]
    ) -> None:
        if not PROFILE_PATH.exists():
            pytest.skip("profile not found")
        tracker = str(tmp_path / "applications.yaml")
        base = ["daily", "--tracker", tracker, "--send", "--to", "U1"]
        quiet = runner.invoke(app, [*base, "--seed", _seed_closing_in(tmp_path, 8)])
        assert quiet.exit_code == 0, quiet.output
        assert "今天沒有需要提醒的事項" in quiet.output
        assert line_requests == []
        due = runner.invoke(app, [*base, "--seed", _seed_closing_in(tmp_path, 7)])
        assert due.exit_code == 0, due.output
        assert "剩 7 天" in due.output
        body = json.loads(line_requests[0].content)
        assert "Soon Grant" in body["messages"][0]["text"]

    def test_daily_fetch_baseline_then_new_announcement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if not PROFILE_PATH.exists():
            pytest.skip("profile not found")
        page = tmp_path / "page.html"

        async def fake_fetch_all(targets: list[tuple[str, str]], raw_dir: Path) -> list[FetchOutcome]:
            return [
                FetchOutcome(source_id=sid, url=url, ok=True, snapshot_path=str(page))
                for sid, url in targets
            ]

        monkeypatch.setattr("subsidy_radar.monitor.fetch_all", fake_fetch_all)
        sources = tmp_path / "sources.yaml"
        sources.write_text(
            f"sources:\n  - id: {SOURCE.id}\n    name: {SOURCE.name}\n    url: {SOURCE.url}\n",
            encoding="utf-8",
        )
        state = tmp_path / "state.json"
        args = [
            "daily", "--fetch", "--seed", _seed_closing_in(tmp_path, 8),
            "--tracker", str(tmp_path / "applications.yaml"),
            "--sources", str(sources), "--state", str(state),
        ]
        page.write_text(_page(("115年度補助計畫", "/n/1")), encoding="utf-8")
        first = runner.invoke(app, args)
        assert first.exit_code == 0, first.output
        assert "今天沒有需要提醒的事項" in first.output
        assert json.loads(state.read_text(encoding="utf-8")) == {"gov": ["https://example.gov.tw/n/1"]}

        page.write_text(
            _page(("115年度補助計畫", "/n/1"), ("116年度補助計畫受理申請", "/n/2")), encoding="utf-8"
        )
        second = runner.invoke(app, args)
        assert second.exit_code == 0, second.output
        assert "116年度補助計畫受理申請" in second.output
        assert "https://example.gov.tw/n/2" in second.output
