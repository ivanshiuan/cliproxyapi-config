"""subsidy_radar 單元測試 — 不需網路、不需 LLM、不需 DB。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from subsidy_radar.distill import (
    _extract_amount,
    _extract_dates,
    _extract_rate,
    _guess_category,
    heuristic_distill,
)
from subsidy_radar.extract import html_links, html_to_text
from subsidy_radar.match import (
    _amount_bonus,
    _deadline_bonus,
    _LLMJudgment,
    _region_matches,
    llm_match_one,
    llm_refine_all,
    match_all,
    match_one,
)
from subsidy_radar.models import (
    CompanyProfile,
    DataConfidence,
    EligibilityRule,
    MatchResult,
    ProgramCategory,
    ProgramStatus,
    RadarReport,
    SubsidyProgram,
    Verdict,
)
from subsidy_radar.pipeline import load_profile, load_programs_from_seed, run_offline
from subsidy_radar.plan import generate_plans
from subsidy_radar.report import generate_report, render_action_plan, render_summary_table

# ── fixtures ──────────────────────────────────────────────────────────────

SEED_PATH = Path("data/subsidies/seed_2026.json")
PROFILE_PATH = Path("data/subsidies/company_profile.yaml")
TODAY = date(2026, 10, 26)


def _program(**overrides: Any) -> SubsidyProgram:
    fields: dict[str, Any] = {
        "id": "p",
        "name": "Program",
        "agency": "Agency",
        "category": ProgramCategory.grant,
        "status": ProgramStatus.open,
        "as_of": date(2026, 10, 1),
        "confidence": DataConfidence.manual,
    }
    fields.update(overrides)
    return SubsidyProgram(**fields)


@pytest.fixture
def sample_program() -> SubsidyProgram:
    return SubsidyProgram(
        id="test-grant",
        name="Test Grant",
        agency="Test Agency",
        category=ProgramCategory.grant,
        status=ProgramStatus.open,
        max_amount_twd=Decimal("1500000"),
        eligibility=EligibilityRule(
            max_employees=200,
            max_capital_twd=Decimal("100000000"),
            industry_scopes=["45-96"],
        ),
        as_of=date(2026, 8, 1),
        confidence=DataConfidence.manual,
    )


@pytest.fixture
def sample_profile() -> CompanyProfile:
    return CompanyProfile(
        company_name="Test Restaurant",
        industry_code="5610",
        industry_desc="Restaurants",
        employee_count=15,
        capital_twd=Decimal("5000000"),
        founded=date(2023, 6, 1),
        region="Taipei",
    )


# ── models ────────────────────────────────────────────────────────────────


class TestModels:
    def test_twd_rejects_float(self) -> None:
        with pytest.raises(ValueError, match="float"):
            EligibilityRule(max_capital_twd=1.5)  # type: ignore[arg-type]

    def test_twd_accepts_string(self) -> None:
        r = EligibilityRule(max_capital_twd="100000000")
        assert r.max_capital_twd == Decimal("100000000")

    def test_twd_strips_comma(self) -> None:
        r = EligibilityRule(max_capital_twd="100,000,000")
        assert r.max_capital_twd == Decimal("100000000")

    def test_subsidy_program_frozen(self) -> None:
        p = SubsidyProgram(
            id="x",
            name="x",
            agency="x",
            category=ProgramCategory.grant,
            status=ProgramStatus.open,
            as_of=date(2026, 1, 1),
            confidence=DataConfidence.manual,
        )
        with pytest.raises(ValidationError):
            p.name = "y"  # type: ignore[misc]


# ── extract ───────────────────────────────────────────────────────────────


class TestExtract:
    def test_html_to_text(self) -> None:
        html = "<p>Hello <b>World</b></p><script>evil()</script>"
        text = html_to_text(html)
        assert "Hello" in text
        assert "World" in text
        assert "evil" not in text

    def test_html_links(self) -> None:
        html = '<a href="https://example.com">Link</a><a href="/local">No</a>'
        links = html_links(html)
        assert links == ["https://example.com"]


# ── distill ───────────────────────────────────────────────────────────────


class TestDistill:
    def test_extract_amount_basic(self) -> None:
        assert _extract_amount("補助上限新臺幣 150 萬元") == Decimal("1500000")

    def test_extract_amount_with_comma(self) -> None:
        assert _extract_amount("最高 1,500,000 元") == Decimal("1500000")

    def test_extract_amount_none(self) -> None:
        assert _extract_amount("no amount here") is None

    def test_extract_rate(self) -> None:
        assert _extract_rate("補助 50%") == "50%"

    def test_extract_rate_none(self) -> None:
        assert _extract_rate("no rate") is None

    def test_extract_dates_roc_year(self) -> None:
        dates = _extract_dates("115 年 1 月 30 日截止")
        assert date(2026, 1, 30) in dates

    def test_guess_category(self) -> None:
        assert _guess_category("貸款利息補貼") == ProgramCategory.loan
        assert _guess_category("節能設備") == ProgramCategory.energy
        assert _guess_category("人力提升計畫") == ProgramCategory.training

    def test_heuristic_distill(self) -> None:
        text = "SBIR 小型企業創新研發計畫\n補助上限新臺幣150萬元\n隨到隨審"
        prog = heuristic_distill("test", text, today=date(2026, 8, 1))
        assert prog.id == "test"
        assert prog.max_amount_twd == Decimal("1500000")
        assert prog.confidence == DataConfidence.heuristic


# ── match ─────────────────────────────────────────────────────────────────


class TestMatch:
    def test_eligible(
        self, sample_program: SubsidyProgram, sample_profile: CompanyProfile
    ) -> None:
        result = match_one(sample_program, sample_profile, today=date(2026, 8, 1))
        assert result.verdict in {Verdict.eligible, Verdict.likely, Verdict.conditional}
        assert result.score > 0
        assert len(result.passes) > 0

    def test_ineligible_too_many_employees(
        self, sample_program: SubsidyProgram
    ) -> None:
        big_co = CompanyProfile(
            company_name="Big Corp",
            industry_code="5610",
            industry_desc="Restaurant",
            employee_count=500,
            capital_twd=Decimal("5000000"),
            founded=date(2020, 1, 1),
            region="Taipei",
        )
        result = match_one(sample_program, big_co, today=date(2026, 8, 1))
        assert result.verdict == Verdict.ineligible
        assert any("員工數" in f for f in result.hard_fails)

    def test_ineligible_wrong_industry(
        self, sample_profile: CompanyProfile
    ) -> None:
        finance_only = SubsidyProgram(
            id="finance-only",
            name="Finance Grant",
            agency="MOF",
            category=ProgramCategory.grant,
            status=ProgramStatus.open,
            eligibility=EligibilityRule(industry_scopes=["64"]),
            as_of=date(2026, 8, 1),
            confidence=DataConfidence.manual,
        )
        result = match_one(finance_only, sample_profile, today=date(2026, 8, 1))
        assert result.verdict == Verdict.ineligible

    def test_match_all_sorted(
        self, sample_program: SubsidyProgram, sample_profile: CompanyProfile
    ) -> None:
        results = match_all([sample_program], sample_profile, today=date(2026, 8, 1))
        assert len(results) == 1
        assert results[0].program_id == "test-grant"


class TestMatchRules:
    def test_region_normalizes_tai_and_suffix(self) -> None:
        assert _region_matches("台北市", ["臺北市"])
        assert _region_matches("臺北", ["台北市"])
        assert not _region_matches("新北市", ["台北市"])
        assert _region_matches("高雄市", [])

    def test_region_mismatch_is_hard_fail(self, sample_profile: CompanyProfile) -> None:
        prog = _program(eligibility=EligibilityRule(allowed_regions=["高雄市"]))
        result = match_one(prog, sample_profile, today=TODAY)
        assert result.verdict == Verdict.ineligible
        assert any("地區" in f for f in result.hard_fails)

    @pytest.mark.parametrize(
        ("days", "bonus"), [(-1, 0), (0, 8), (30, 8), (31, 4), (90, 4), (91, 1)]
    )
    def test_deadline_bonus(self, days: int, bonus: int) -> None:
        assert _deadline_bonus(TODAY + timedelta(days=days), TODAY) == bonus

    def test_deadline_bonus_rolling(self) -> None:
        assert _deadline_bonus(None, TODAY) == 0

    @pytest.mark.parametrize(
        ("amount", "bonus"),
        [(None, 0), ("99999", 0), ("100000", 1), ("500000", 3), ("1000000", 5)],
    )
    def test_amount_bonus(self, amount: str | None, bonus: int) -> None:
        assert _amount_bonus(_program(max_amount_twd=amount)) == bonus

    def test_closed_with_next_round_is_watch(self, sample_profile: CompanyProfile) -> None:
        prog = _program(
            status=ProgramStatus.closed,
            window_close=date(2026, 6, 30),
            next_round_hint="2027 年再開",
        )
        result = match_one(prog, sample_profile, today=TODAY)
        assert result.verdict == Verdict.watch
        assert result.hard_fails == []

    def test_closed_without_next_round_is_ineligible(
        self, sample_profile: CompanyProfile
    ) -> None:
        prog = _program(status=ProgramStatus.closed, window_close=date(2026, 6, 30))
        result = match_one(prog, sample_profile, today=TODAY)
        assert result.verdict == Verdict.ineligible
        assert result.score == 0

    def test_closed_with_hard_fail_stays_ineligible(
        self, sample_profile: CompanyProfile
    ) -> None:
        prog = _program(
            status=ProgramStatus.closed,
            next_round_hint="2027 年再開",
            eligibility=EligibilityRule(industry_scopes=["64"]),
        )
        result = match_one(prog, sample_profile, today=TODAY)
        assert result.verdict == Verdict.ineligible


# ── llm refine (fake client, no API calls) ────────────────────────────────


class _FakeMessages:
    def __init__(self, judgment: _LLMJudgment | None, stop_reason: str) -> None:
        self.judgment = judgment
        self.stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.judgment)


def _fake_client(judgment: _LLMJudgment | None, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(beta=SimpleNamespace(messages=_FakeMessages(judgment, stop_reason)))


def _judgment(**overrides: Any) -> _LLMJudgment:
    fields: dict[str, Any] = {
        "verdict": "eligible",
        "hard_fails": [],
        "uncertainties": [],
        "strategic_notes": "先備妥勞保投保清冊",
        "recommended_angle": "AI 排班降低人力成本",
    }
    fields.update(overrides)
    return _LLMJudgment(**fields)


class TestLLMMatch:
    async def test_llm_cannot_upgrade_verdict(
        self, sample_program: SubsidyProgram, sample_profile: CompanyProfile
    ) -> None:
        client = _fake_client(_judgment(verdict="eligible"))
        result = await llm_match_one(sample_program, sample_profile, client, today=TODAY)
        heuristic = match_one(sample_program, sample_profile, today=TODAY)
        assert result.verdict == heuristic.verdict == Verdict.likely
        assert result.score == heuristic.score
        assert result.recommended_angle == "AI 排班降低人力成本"
        call = client.beta.messages.calls[0]
        assert call["output_format"] is _LLMJudgment
        assert call["fallbacks"] == "default"

    async def test_llm_ineligible_is_flagged_not_dropped(
        self, sample_program: SubsidyProgram, sample_profile: CompanyProfile
    ) -> None:
        client = _fake_client(_judgment(verdict="ineligible", hard_fails=["限製造業申請"]))
        result = await llm_match_one(sample_program, sample_profile, client, today=TODAY)
        assert result.verdict == Verdict.conditional
        assert any("限製造業申請" in u for u in result.uncertainties)
        assert result.hard_fails == []

    async def test_llm_cannot_reopen_closed_program(
        self, sample_profile: CompanyProfile
    ) -> None:
        closed = _program(status=ProgramStatus.closed, next_round_hint="2027 年再開")
        client = _fake_client(_judgment(verdict="eligible"))
        result = await llm_match_one(closed, sample_profile, client, today=TODAY)
        assert result.verdict == Verdict.watch

    async def test_refine_all_only_calls_llm_for_actionable(
        self, sample_program: SubsidyProgram, sample_profile: CompanyProfile
    ) -> None:
        closed = _program(id="closed", status=ProgramStatus.closed, next_round_hint="2027")
        programs = [sample_program, closed]
        matches = match_all(programs, sample_profile, today=TODAY)
        client = _fake_client(_judgment())
        refined = await llm_refine_all(programs, matches, sample_profile, TODAY, client=client)
        assert len(client.beta.messages.calls) == 1
        by_id = {m.program_id: m for m in refined}
        assert by_id["test-grant"].strategic_notes
        assert by_id["closed"].verdict == Verdict.watch

    async def test_refine_all_keeps_rule_result_on_refusal(
        self, sample_program: SubsidyProgram, sample_profile: CompanyProfile
    ) -> None:
        matches = match_all([sample_program], sample_profile, today=TODAY)
        client = _fake_client(None, stop_reason="refusal")
        refined = await llm_refine_all(
            [sample_program], matches, sample_profile, TODAY, client=client
        )
        assert refined == matches


# ── plan ──────────────────────────────────────────────────────────────────


class TestPlan:
    def test_generate_plans(
        self, sample_program: SubsidyProgram, sample_profile: CompanyProfile
    ) -> None:
        matches = match_all([sample_program], sample_profile, today=date(2026, 8, 1))
        plans = generate_plans([sample_program], matches, today=date(2026, 8, 1))
        actionable = [
            m
            for m in matches
            if m.verdict in {Verdict.eligible, Verdict.likely, Verdict.conditional}
        ]
        assert len(plans) == len(actionable)
        if plans:
            assert plans[0].program_id == "test-grant"
            assert len(plans[0].steps) > 0

    def test_urgent_first_with_official_deadline(self, sample_profile: CompanyProfile) -> None:
        urgent = _program(id="urgent", name="Urgent", window_close=date(2026, 10, 31))
        rolling = _program(
            id="rolling",
            name="Rolling",
            status=ProgramStatus.rolling,
            max_amount_twd="5000000",
            eligibility=EligibilityRule(
                max_employees=200, max_capital_twd="100000000", industry_scopes=["45-96"]
            ),
        )
        programs = [urgent, rolling]
        matches = match_all(programs, sample_profile, today=TODAY)
        assert matches[0].program_id == "rolling"
        plans = generate_plans(programs, matches, today=TODAY)
        assert [p.program_id for p in plans] == ["urgent", "rolling"]
        assert plans[0].deadline == date(2026, 10, 31)
        assert "剩 5 天" in plans[0].urgency
        assert "2026-10-31" in render_action_plan(plans[0])


# ── report ────────────────────────────────────────────────────────────────


class TestReport:
    def test_render_summary_table(self) -> None:
        mr = MatchResult(
            program_id="x",
            program_name="Test",
            verdict=Verdict.eligible,
            score=70,
        )
        table = render_summary_table([mr])
        assert "Test" in table
        assert "✅" in table

    def test_generate_report(
        self, sample_program: SubsidyProgram, sample_profile: CompanyProfile
    ) -> None:
        matches = match_all([sample_program], sample_profile, today=date(2026, 8, 1))
        plans = generate_plans([sample_program], matches, today=date(2026, 8, 1))
        report = RadarReport(
            generated_at=datetime(2026, 8, 1, tzinfo=UTC),
            profile=sample_profile,
            programs=[sample_program],
            matches=matches,
            plans=plans,
        )
        md = generate_report(report)
        assert "雷達報告" in md
        assert sample_profile.company_name in md


# ── pipeline (offline, from real seed) ────────────────────────────────────


class TestPipeline:
    def test_load_seed(self) -> None:
        if not SEED_PATH.exists():
            pytest.skip("seed file not found")
        programs = load_programs_from_seed(SEED_PATH)
        assert len(programs) >= 19
        for p in programs:
            assert p.id
            assert p.confidence in DataConfidence

    def test_load_profile(self) -> None:
        if not PROFILE_PATH.exists():
            pytest.skip("profile file not found")
        profile = load_profile(PROFILE_PATH)
        assert profile.company_name
        assert profile.capital_twd > 0

    def test_run_offline(self, tmp_path: Path) -> None:
        if not SEED_PATH.exists() or not PROFILE_PATH.exists():
            pytest.skip("seed or profile not found")
        output = tmp_path / "radar_test.md"
        report = run_offline(SEED_PATH, PROFILE_PATH, output, today=date(2026, 8, 4))
        assert output.exists()
        content = output.read_text()
        assert "雷達報告" in content
        assert len(report.matches) >= 19
        eligible_count = sum(
            1
            for m in report.matches
            if m.verdict in {Verdict.eligible, Verdict.likely, Verdict.conditional}
        )
        assert eligible_count >= 3
