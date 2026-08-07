"""Pipeline 編排 — 把各模組串成「抓→蒸餾→配→包→報告」一條龍。

兩種模式：
1. offline — 從 seed JSON + company YAML 跑（免網路、免 LLM）
2. online  — httpx 抓取 + LLM 蒸餾（需 ANTHROPIC_API_KEY）
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from subsidy_radar.match import match_all
from subsidy_radar.models import (
    CompanyProfile,
    RadarReport,
    SubsidyProgram,
)
from subsidy_radar.plan import generate_plans
from subsidy_radar.report import generate_report


def load_programs_from_seed(seed_path: Path) -> list[SubsidyProgram]:
    """從 seed JSON 載入已蒸餾好的補助卡（離線模式）。"""
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    programs: list[SubsidyProgram] = []
    for item in raw:
        programs.append(SubsidyProgram.model_validate(item))
    return programs


def load_profile(profile_path: Path) -> CompanyProfile:
    """從 YAML 載入公司側寫。"""
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    return CompanyProfile.model_validate(raw)


def run_offline(
    seed_path: Path,
    profile_path: Path,
    output_path: Path | None = None,
    today: date | None = None,
) -> RadarReport:
    """離線完整 pipeline：seed JSON + profile YAML → 雷達報告。"""
    today = today or date.today()
    programs = load_programs_from_seed(seed_path)
    profile = load_profile(profile_path)
    matches = match_all(programs, profile, today=today)
    plans = generate_plans(programs, matches, today=today)

    report = RadarReport(
        generated_at=datetime.now(UTC),
        profile=profile,
        programs=programs,
        matches=matches,
        plans=plans,
    )

    md = generate_report(report)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md, encoding="utf-8")

    return report
