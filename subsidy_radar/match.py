"""資格配對引擎 — SubsidyProgram × CompanyProfile → MatchResult。

門檻三級：
1. hard_fail   — 一票否決（資本額/員工數/行業別/設立年限不符）
2. uncertainty — 缺資料 or 需線下文件確認
3. pass_       — 確認通過的條件

score 0-100 = 基底 50 + pass 各 +5 + hard_fail 各 -50 + uncertainty 各 -3
"""

from __future__ import annotations

from datetime import date

from subsidy_radar.models import (
    CompanyProfile,
    MatchResult,
    SubsidyProgram,
    Verdict,
)


def _industry_matches(profile_code: str, scopes: list[str]) -> bool:
    """行業代碼配對：scope 可以是前綴 or 區間（"45-96"）。"""
    if not scopes:
        return True
    code2 = profile_code[:2]
    for scope in scopes:
        if "-" in scope:
            lo, hi = scope.split("-", 1)
            if lo.isdigit() and hi.isdigit() and lo <= code2 <= hi:
                return True
        elif profile_code.startswith(scope):
            return True
    return False


def match_one(
    program: SubsidyProgram,
    profile: CompanyProfile,
    today: date | None = None,
) -> MatchResult:
    today = today or date.today()
    passes: list[str] = []
    hard_fails: list[str] = []
    uncertainties: list[str] = []

    elig = program.eligibility

    if elig.max_employees is not None:
        if profile.employee_count <= elig.max_employees:
            passes.append(f"員工數 {profile.employee_count} <= 上限 {elig.max_employees}")
        else:
            hard_fails.append(
                f"員工數 {profile.employee_count} > 上限 {elig.max_employees}"
            )

    if elig.min_employees is not None:
        if profile.employee_count >= elig.min_employees:
            passes.append(f"員工數 {profile.employee_count} >= 下限 {elig.min_employees}")
        else:
            hard_fails.append(
                f"員工數 {profile.employee_count} < 下限 {elig.min_employees}"
            )

    if elig.max_capital_twd is not None:
        if profile.capital_twd <= elig.max_capital_twd:
            passes.append(
                f"資本額 {profile.capital_twd:,} <= 上限 {elig.max_capital_twd:,}"
            )
        else:
            hard_fails.append(
                f"資本額 {profile.capital_twd:,} > 上限 {elig.max_capital_twd:,}"
            )

    if elig.industry_scopes:
        if _industry_matches(profile.industry_code, elig.industry_scopes):
            passes.append(f"行業 {profile.industry_code} 符合 {elig.industry_scopes}")
        else:
            hard_fails.append(
                f"行業 {profile.industry_code} 不在 {elig.industry_scopes} 範圍"
            )

    if elig.company_age_max_years is not None:
        company_age = (today - profile.founded).days / 365.25
        if company_age <= elig.company_age_max_years:
            passes.append(f"公司年齡 {company_age:.1f} 年 <= {elig.company_age_max_years}")
        else:
            hard_fails.append(
                f"公司年齡 {company_age:.1f} 年 > {elig.company_age_max_years}"
            )

    if elig.owner_age_min is not None or elig.owner_age_max is not None:
        if profile.owner_age is None:
            uncertainties.append("需確認負責人年齡")
        else:
            if elig.owner_age_min and profile.owner_age < elig.owner_age_min:
                hard_fails.append(
                    f"負責人年齡 {profile.owner_age} < {elig.owner_age_min}"
                )
            elif elig.owner_age_max and profile.owner_age > elig.owner_age_max:
                hard_fails.append(
                    f"負責人年齡 {profile.owner_age} > {elig.owner_age_max}"
                )
            else:
                passes.append(f"負責人年齡 {profile.owner_age} 符合")

    for req in elig.requires:
        uncertainties.append(f"需確認：{req}")

    if program.window_close and program.window_close < today:
        hard_fails.append(f"申請已截止（{program.window_close}）")

    if profile.has_tax_debt is True:
        uncertainties.append("有欠稅紀錄，多數計畫會排除")
    elif profile.has_tax_debt is None:
        uncertainties.append("欠稅狀態未知，建議先查國稅局")

    if profile.has_labor_law_violations is True:
        uncertainties.append("有勞動法規違規，部分計畫會排除")
    elif profile.has_labor_law_violations is None:
        uncertainties.append("勞動法規違規狀態未知")

    score = 50
    score += len(passes) * 5
    score -= len(hard_fails) * 50
    score -= len(uncertainties) * 3
    score = max(0, min(100, score))

    if hard_fails:
        verdict = Verdict.ineligible
    elif not uncertainties:
        verdict = Verdict.eligible
    elif len(uncertainties) <= 2 and score >= 50:
        verdict = Verdict.likely
    elif program.window_close and program.window_close < today:
        verdict = Verdict.watch
    else:
        verdict = Verdict.conditional

    estimated_value = program.max_amount_twd

    return MatchResult(
        program_id=program.id,
        program_name=program.name,
        verdict=verdict,
        score=score,
        passes=passes,
        hard_fails=hard_fails,
        uncertainties=uncertainties,
        estimated_value_twd=estimated_value,
        reasoning=f"通過 {len(passes)} 條、否決 {len(hard_fails)} 條、待確認 {len(uncertainties)} 條",
    )


def match_all(
    programs: list[SubsidyProgram],
    profile: CompanyProfile,
    today: date | None = None,
) -> list[MatchResult]:
    results = [match_one(p, profile, today=today) for p in programs]
    return sorted(results, key=lambda r: (-r.score, r.program_name))
