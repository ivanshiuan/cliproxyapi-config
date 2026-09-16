"""subsidy_radar 單元測試 — 不需網路、不需 LLM、不需 DB。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

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
from subsidy_radar.match import match_all, match_one
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
from subsidy_radar.report import generate_report, render_summary_table

# ── fixtures ──────────────────────────────────────────────────────────────

SEED_PATH = Path("data/subsidies/seed_2026.json")
PROFILE_PATH = Path("data/subsidies/company_profile.yaml")


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
        assert len(programs) >= 9
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
        assert len(report.matches) >= 9
        eligible_count = sum(
            1
            for m in report.matches
            if m.verdict in {Verdict.eligible, Verdict.likely, Verdict.conditional}
        )
        assert eligible_count >= 3
