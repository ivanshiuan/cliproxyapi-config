"""資格配對引擎 — SubsidyProgram × CompanyProfile → MatchResult。

門檻三級：
1. hard_fail   — 一票否決（資本額/員工數/行業別/設立年限/地區不符）
2. uncertainty — 缺資料 or 需線下文件確認
3. pass_       — 確認通過的條件

score 0-100 = 基底 50 + pass 各 +5 + hard_fail 各 -50 + uncertainty 各 -3
              + 金額加成 + 截止日臨近加成

已截止但有 next_round_hint 的案件 → watch（追蹤下一輪），不是 ineligible。
"""

from __future__ import annotations

import json
import os
from datetime import date

from subsidy_radar.models import (
    CompanyProfile,
    MatchResult,
    ProgramStatus,
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


def _region_matches(profile_region: str, allowed: list[str]) -> bool:
    """地區配對：allowed 為空表示不限地區。"""
    if not allowed:
        return True
    region = profile_region.replace("市", "").replace("縣", "").strip()
    for a in allowed:
        norm = a.replace("市", "").replace("縣", "").strip()
        if norm in region or region in norm:
            return True
    return False


def _deadline_bonus(window_close: date | None, today: date) -> int:
    """截止日越近，分數加越多（催促儘早行動）。30 天內 +8、90 天內 +4。"""
    if not window_close:
        return 0
    days_left = (window_close - today).days
    if days_left < 0:
        return 0
    if days_left <= 30:
        return 8
    if days_left <= 90:
        return 4
    return 1


def _amount_bonus(program: SubsidyProgram) -> int:
    """高額補助加分：>= 100 萬 +5、>= 50 萬 +3、>= 10 萬 +1。"""
    amt = program.max_amount_twd
    if amt is None:
        return 0
    if amt >= 1_000_000:
        return 5
    if amt >= 500_000:
        return 3
    if amt >= 100_000:
        return 1
    return 0


def match_one(
    program: SubsidyProgram,
    profile: CompanyProfile,
    today: date | None = None,
) -> MatchResult:
    today = today or date.today()
    passes: list[str] = []
    hard_fails: list[str] = []
    uncertainties: list[str] = []
    is_expired = False

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

    if elig.allowed_regions:
        if _region_matches(profile.region, elig.allowed_regions):
            passes.append(f"地區 {profile.region} 符合 {elig.allowed_regions}")
        else:
            hard_fails.append(
                f"地區 {profile.region} 不在 {elig.allowed_regions} 範圍"
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
        is_expired = True

    if program.status == ProgramStatus.closed and not is_expired:
        is_expired = True

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
    score += _deadline_bonus(program.window_close, today)
    score += _amount_bonus(program)
    score = max(0, min(100, score))

    if is_expired and not hard_fails:
        if program.next_round_hint:
            verdict = Verdict.watch
            score = max(score, 20)
        else:
            hard_fails.append(f"申請已截止（{program.window_close or program.status}）")
            verdict = Verdict.ineligible
            score = 0
    elif is_expired and hard_fails:
        hard_fails.append(f"申請已截止（{program.window_close or program.status}）")
        verdict = Verdict.ineligible
        score = 0
    elif hard_fails:
        verdict = Verdict.ineligible
    elif not uncertainties:
        verdict = Verdict.eligible
    elif len(uncertainties) <= 2 and score >= 50:
        verdict = Verdict.likely
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


_LLM_MATCH_SYSTEM = """你是台灣政府補助案資格審查專家。使用者會提供一個補助案和一間公司的資料。
請分析這間公司是否符合該補助案的資格，輸出 JSON：
{
  "verdict": "eligible|likely|conditional|watch|ineligible",
  "confidence": 0.0-1.0,
  "passes": ["通過的條件..."],
  "hard_fails": ["不符合的條件..."],
  "uncertainties": ["待確認的事項..."],
  "strategic_notes": "對這間公司的具體申請建議（200字內）",
  "recommended_angle": "建議的申請切角（如何包裝題目讓審查委員認可）"
}
不要加 markdown 格式。直接回 JSON。"""


async def llm_match_one(
    program: SubsidyProgram,
    profile: CompanyProfile,
    today: date | None = None,
    api_key: str | None = None,
) -> MatchResult:
    """用 Claude 做深度資格分析，回傳精準 MatchResult + 策略建議。"""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("需安裝 anthropic SDK：pip install anthropic") from exc

    today = today or date.today()
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 未設定")

    prompt = f"""## 補助案資料
- 名稱：{program.name}
- 主辦機關：{program.agency}
- 類別：{program.category}
- 狀態：{program.status}
- 申請截止：{program.window_close or '常態受理'}
- 最高金額：{program.max_amount_twd or '未定'}
- 補助比率：{program.subsidy_rate or '未定'}
- 金額說明：{program.amount_notes or ''}
- 資格條件：{program.eligibility.model_dump_json(indent=2)}
- 申請方式：{program.how_to_apply or ''}
- 備註：{program.notes or ''}

## 公司資料
- 公司名稱：{profile.company_name}
- 行業：{profile.industry_desc}（{profile.industry_code}）
- 員工數：{profile.employee_count}
- 資本額：{profile.capital_twd:,} 元
- 設立日期：{profile.founded}
- 地區：{profile.region}
- 負責人年齡：{profile.owner_age or '未知'}
- 公司登記：{'是' if profile.is_company_registered else '否'}
- 數位工具：{', '.join(profile.digital_tools) if profile.digital_tools else '未知'}

## 今天日期：{today}

請分析這間公司申請此補助案的資格與策略。"""

    client = anthropic.AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=_LLM_MATCH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    data = json.loads(raw)

    verdict_map = {v.value: v for v in Verdict}
    verdict = verdict_map.get(data.get("verdict", "unknown"), Verdict.unknown)

    heuristic = match_one(program, profile, today=today)
    merged_passes = list(dict.fromkeys(heuristic.passes + data.get("passes", [])))
    merged_fails = list(dict.fromkeys(heuristic.hard_fails + data.get("hard_fails", [])))
    merged_unc = list(dict.fromkeys(heuristic.uncertainties + data.get("uncertainties", [])))

    strategic = data.get("strategic_notes", "")
    angle = data.get("recommended_angle", "")
    reasoning = f"通過 {len(merged_passes)} 條、否決 {len(merged_fails)} 條、待確認 {len(merged_unc)} 條"
    if strategic:
        reasoning += f"\n策略：{strategic}"
    if angle:
        reasoning += f"\n切角：{angle}"

    return MatchResult(
        program_id=program.id,
        program_name=program.name,
        verdict=verdict if not merged_fails else Verdict.ineligible,
        score=heuristic.score,
        passes=merged_passes,
        hard_fails=merged_fails,
        uncertainties=merged_unc,
        estimated_value_twd=program.max_amount_twd,
        reasoning=reasoning,
    )
