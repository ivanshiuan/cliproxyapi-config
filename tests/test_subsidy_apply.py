"""subsidy_radar Phase 4 — 申請追蹤、代擬草稿、提醒。不打網路、不打 API、不需 DB。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from restaurant_api.integrations.line import HttpLineMessenger, StubLineMessenger
from subsidy_radar.cli import app
from subsidy_radar.draft import DraftError, draft_application, render_draft
from subsidy_radar.match import match_one
from subsidy_radar.models import (
    CompanyProfile,
    DataConfidence,
    DocStatus,
    MatchResult,
    ProgramCategory,
    ProgramStatus,
    SubsidyProgram,
    Verdict,
)
from subsidy_radar.pipeline import load_programs_from_seed, run_offline
from subsidy_radar.plan import generate_plan
from subsidy_radar.remind import build_reminders, push_reminders, render_reminder_text
from subsidy_radar.tracker import (
    load_applications,
    missing_documents,
    progress,
    resolve_document,
    save_applications,
    set_document,
    start_application,
)

SEED_PATH = Path("data/subsidies/seed_2026.json")
PROFILE_PATH = Path("data/subsidies/company_profile.yaml")
TODAY = date(2026, 10, 29)
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


@pytest.fixture
def profile() -> CompanyProfile:
    return CompanyProfile(
        company_name="Test Restaurant",
        industry_code="5610",
        industry_desc="Restaurants",
        employee_count=15,
        capital_twd=Decimal("5000000"),
        founded=date(2023, 6, 1),
        region="台北市",
    )


def _seed_program(program_id: str) -> SubsidyProgram:
    return next(p for p in load_programs_from_seed(SEED_PATH) if p.id == program_id)


# ── tracker ───────────────────────────────────────────────────────────────


class TestTracker:
    def test_start_application_lists_required_documents_as_todo(self) -> None:
        application = start_application(_program(), today=TODAY)
        assert application.started == TODAY
        assert application.documents == {"計畫書": DocStatus.todo, "公司登記證明": DocStatus.todo}

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "applications.yaml"
        application = set_document(start_application(_program(), today=TODAY), "計畫書", DocStatus.done)
        save_applications(path, {"p": application})
        text = path.read_text(encoding="utf-8")
        assert "計畫書: done" in text
        assert text.startswith("# 補助申請追蹤")
        assert load_applications(path) == {"p": application}

    def test_load_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_applications(tmp_path / "nope.yaml") == {}

    def test_progress_ignores_not_applicable(self) -> None:
        application = start_application(_program(required_documents=["a", "b", "c"]), today=TODAY)
        application = set_document(application, "a", DocStatus.done)
        application = set_document(application, "b", DocStatus.na)
        assert progress(application) == (1, 2)
        assert missing_documents(application) == ["c"]

    def test_resolve_document_by_index_or_name(self) -> None:
        application = start_application(_program(), today=TODAY)
        assert resolve_document(application, "2") == "公司登記證明"
        assert resolve_document(application, "計畫書") == "計畫書"
        assert resolve_document(application, "9") == "9"


# ── draft ─────────────────────────────────────────────────────────────────


class _FakeStream:
    def __init__(self, message: SimpleNamespace) -> None:
        self._message = message

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> SimpleNamespace:
        return self._message


class _FakeBetaMessages:
    def __init__(self, message: SimpleNamespace) -> None:
        self.message = message
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        return _FakeStream(self.message)


def _fake_client(
    text: str = "## 計畫緣起\n內容", stop_reason: str = "end_turn", leading_blocks: tuple[Any, ...] = ()
) -> Any:
    content = [*leading_blocks, SimpleNamespace(type="text", text=text)]
    message = SimpleNamespace(stop_reason=stop_reason, content=content)
    return SimpleNamespace(beta=SimpleNamespace(messages=_FakeBetaMessages(message)))


class TestDraft:
    async def test_draft_streams_with_case_context(
        self, profile: CompanyProfile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUBSIDY_RADAR_MODEL", raising=False)
        client = _fake_client(leading_blocks=(SimpleNamespace(type="thinking", thinking=""),))
        body = await draft_application(_program(), profile, client, today=TODAY)
        assert body == "## 計畫緣起\n內容"
        call = client.beta.messages.calls[0]
        assert call["model"] == "claude-opus-5-5"
        assert call["fallbacks"] == "default"
        prompt = call["messages"][0]["content"]
        assert "Test Restaurant" in prompt
        assert "規則判定" in prompt

    @pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
    async def test_draft_rejects_incomplete_output(
        self, profile: CompanyProfile, stop_reason: str
    ) -> None:
        with pytest.raises(DraftError):
            await draft_application(
                _program(), profile, _fake_client(stop_reason=stop_reason), today=TODAY
            )

    def test_render_draft_header(self) -> None:
        md = render_draft(_program(name="小人提"), "內文", "claude-opus-5-5", datetime(2026, 10, 28, 16, 30, tzinfo=UTC))
        assert md.startswith("# 申請書草稿")
        assert md.splitlines()[0].endswith("小人提")
        assert "2026-10-29 00:30" in md
        assert "AI 代擬草稿" in md
        assert md.rstrip().endswith("內文")


# ── remind ────────────────────────────────────────────────────────────────


def _match(program_id: str, verdict: Verdict) -> MatchResult:
    return MatchResult(program_id=program_id, program_name=program_id, verdict=verdict, score=50)


class TestRemind:
    def test_build_reminders_picks_deadlines_and_missing_documents(self) -> None:
        programs = [
            _program(id="later", name="Later", window_close=TODAY + timedelta(days=60)),
            _program(id="urgent", name="Urgent", window_close=TODAY + timedelta(days=5)),
            _program(id="untracked", name="Untracked", window_close=TODAY + timedelta(days=60)),
            _program(id="closed", name="Closed", window_close=TODAY + timedelta(days=3)),
        ]
        matches = [
            _match("later", Verdict.conditional),
            _match("urgent", Verdict.likely),
            _match("untracked", Verdict.likely),
            _match("closed", Verdict.watch),
        ]
        applications = {"later": start_application(programs[0], today=TODAY)}
        reminders = build_reminders(programs, matches, applications, TODAY)
        assert [r.program_id for r in reminders] == ["urgent", "later"]
        assert reminders[0].closes_soon and not reminders[0].applying
        assert reminders[1].missing == ["計畫書", "公司登記證明"]

    async def test_render_and_push_via_stub(self) -> None:
        programs = [_program(id="urgent", name="小人提", window_close=TODAY + timedelta(days=2))]
        reminders = build_reminders(programs, [_match("urgent", Verdict.likely)], {}, TODAY)
        text = render_reminder_text(reminders, TODAY)
        assert "小人提" in text
        assert "剩 2 天" in text
        assert "尚未開始申請" in text
        stub = StubLineMessenger()
        await push_reminders(stub, "U123", text)
        assert stub.sent_messages[0]["to"] == "U123"
        assert stub.sent_messages[0]["message"].text == text

    def test_render_truncates_to_line_limit(self) -> None:
        programs = [
            _program(id=f"p{i}", name="很長的補助案名稱" * 10, window_close=TODAY + timedelta(days=1))
            for i in range(100)
        ]
        matches = [_match(p.id, Verdict.likely) for p in programs]
        text = render_reminder_text(build_reminders(programs, matches, {}, TODAY), TODAY)
        assert len(text) <= 5000


# ── plan + report ─────────────────────────────────────────────────────────


def test_plan_steps_point_to_real_commands(profile: CompanyProfile) -> None:
    program = _program(id="sbir")
    plan = generate_plan(program, match_one(program, profile, today=TODAY), priority=1, today=TODAY)
    details = "\n".join(s.detail for s in plan.steps)
    assert "subsidy_radar apply sbir" in details
    assert "subsidy_radar draft sbir" in details


def test_report_renders_tracked_document_checkboxes(tmp_path: Path) -> None:
    if not SEED_PATH.exists() or not PROFILE_PATH.exists():
        pytest.skip("seed or profile not found")
    sbir = _seed_program("sbir-phase1")
    application = set_document(
        start_application(sbir, today=TODAY), sbir.required_documents[0], DocStatus.done
    )
    tracker = tmp_path / "applications.yaml"
    save_applications(tracker, {sbir.id: application})
    output = tmp_path / "radar.md"
    run_offline(SEED_PATH, PROFILE_PATH, output, today=TODAY, tracker_path=tracker)
    md = output.read_text(encoding="utf-8")
    assert f"已備 1/{len(sbir.required_documents)}" in md
    assert f"- [x] {sbir.required_documents[0]}" in md


# ── CLI ───────────────────────────────────────────────────────────────────


class TestCli:
    def test_apply_doc_status_flow(self, tmp_path: Path) -> None:
        tracker = str(tmp_path / "applications.yaml")
        result = runner.invoke(app, ["apply", "small-biz-training", "--tracker", tracker])
        assert result.exit_code == 0, result.output
        result = runner.invoke(app, ["doc", "small-biz-training", "1", "--tracker", tracker])
        assert result.exit_code == 0, result.output
        application = load_applications(Path(tracker))["small-biz-training"]
        assert next(iter(application.documents.values())) == DocStatus.done
        result = runner.invoke(app, ["status", "--tracker", tracker])
        assert result.exit_code == 0, result.output
        assert "1/" in result.output

    def test_unknown_program_and_untracked_doc_fail(self, tmp_path: Path) -> None:
        tracker = str(tmp_path / "applications.yaml")
        assert runner.invoke(app, ["apply", "no-such-program", "--tracker", tracker]).exit_code == 1
        assert runner.invoke(app, ["doc", "sbir-phase1", "1", "--tracker", tracker]).exit_code == 1

    def test_draft_writes_markdown(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _fake_client(text="## 計畫緣起\n草稿內文")
        monkeypatch.setattr("subsidy_radar.cli.anthropic.AsyncAnthropic", lambda: client)
        result = runner.invoke(app, ["draft", "sbir-phase1", "--out-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        md = (tmp_path / "sbir-phase1.md").read_text(encoding="utf-8")
        assert md.startswith("# 申請書草稿")
        assert "草稿內文" in md

    def test_draft_without_credentials_explains(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _NoAuthMessages:
            def stream(self, **kwargs: Any) -> Any:
                raise TypeError("Could not resolve authentication method. Expected one of api_key")

        client = SimpleNamespace(beta=SimpleNamespace(messages=_NoAuthMessages()))
        monkeypatch.setattr("subsidy_radar.cli.anthropic.AsyncAnthropic", lambda: client)
        result = runner.invoke(app, ["draft", "sbir-phase1", "--out-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "ANTHROPIC_API_KEY" in result.output
        assert not (tmp_path / "sbir-phase1.md").exists()

    def _tracker_with_open_application(self, tmp_path: Path) -> str:
        tracker = tmp_path / "applications.yaml"
        save_applications(tracker, {"sbir-phase1": start_application(_seed_program("sbir-phase1"))})
        return str(tracker)

    def test_remind_send_requires_recipient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUBSIDY_LINE_TO", raising=False)
        tracker = self._tracker_with_open_application(tmp_path)
        result = runner.invoke(app, ["remind", "--tracker", tracker, "--send"])
        assert result.exit_code == 1
        assert "SUBSIDY_LINE_TO" in result.output

    def test_remind_send_pushes_to_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={})

        messenger = HttpLineMessenger(
            channel_access_token="test-token", transport=httpx.MockTransport(handler)
        )
        monkeypatch.setattr("subsidy_radar.cli.HttpLineMessenger.from_env", lambda: messenger)
        tracker = self._tracker_with_open_application(tmp_path)
        result = runner.invoke(app, ["remind", "--tracker", tracker, "--send", "--to", "U123"])
        assert result.exit_code == 0, result.output
        assert requests[0].url.path.endswith("/message/push")
        body = json.loads(requests[0].content)
        assert body["to"] == "U123"
        assert "SBIR" in body["messages"][0]["text"]
