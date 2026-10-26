"""資格配對引擎 — SubsidyProgram × CompanyProfile → MatchResult。

門檻三級：
1. hard_fail   — 一票否決（資本額/員工數/行業別/設立年限/地區不符）
2. uncertainty — 缺資料 or 需線下文件確認
3. pass_       — 確認通過的條件

score 0-100 = 基底 50 + pass 各 +5 + hard_fail 各 -50 + uncertainty 各 -3
              + 金額加成 + 截止日臨近加成

尚未開放、或已截止但有 next_round_hint 的案件 → watch（追蹤），不是 ineligible。

LLM 精煉只能讓判定更保守並補上申請策略：截止日與結構化硬門檻由規則決定，
LLM 認為不符的案件降為 conditional 並標註依據，交人工判斷，不會被直接剔除。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from subsidy_radar.models import (
    CompanyProfile,
    MatchResult,
    ProgramStatus,
    SubsidyProgram,
    Verdict,
    taipei_today,
)

logger = logging.getLogger(__name__)

_ACTIONABLE_RANK = {Verdict.eligible: 0, Verdict.likely: 1, Verdict.conditional: 2}


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


def _normalize_region(name: str) -> str:
    return name.replace("臺", "台").replace("市", "").replace("縣", "").strip()


def _region_matches(profile_region: str, allowed: list[str]) -> bool:
    """地區配對：allowed 為空表示不限地區；臺/台、有無市/縣視為相同。"""
    if not allowed:
        return True
    region = _normalize_region(profile_region)
    for a in allowed:
        norm = _normalize_region(a)
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


def _reasoning(passes: list[str], hard_fails: list[str], uncertainties: list[str]) -> str:
    return f"通過 {len(passes)} 條、否決 {len(hard_fails)} 條、待確認 {len(uncertainties)} 條"


def match_one(
    program: SubsidyProgram,
    profile: CompanyProfile,
    today: date | None = None,
) -> MatchResult:
    today = today or taipei_today()
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

    is_expired = program.status == ProgramStatus.closed or (
        program.window_close is not None and program.window_close < today
    )
    not_yet_open = program.status == ProgramStatus.upcoming or (
        program.window_open is not None and program.window_open > today
    )

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
    elif not_yet_open:
        verdict = Verdict.watch
    elif not uncertainties:
        verdict = Verdict.eligible
    elif len(uncertainties) <= 2 and score >= 50:
        verdict = Verdict.likely
    else:
        verdict = Verdict.conditional

    return MatchResult(
        program_id=program.id,
        program_name=program.name,
        verdict=verdict,
        score=score,
        passes=passes,
        hard_fails=hard_fails,
        uncertainties=uncertainties,
        estimated_value_twd=program.max_amount_twd,
        reasoning=_reasoning(passes, hard_fails, uncertainties),
    )


def match_all(
    programs: list[SubsidyProgram],
    profile: CompanyProfile,
    today: date | None = None,
) -> list[MatchResult]:
    results = [match_one(p, profile, today=today) for p in programs]
    return sorted(results, key=lambda r: (-r.score, r.program_name))


# ── LLM 精煉 ──────────────────────────────────────────────────────────────

_LLM_MODEL_DEFAULT = "claude-opus-5-5"

_LLM_MATCH_SYSTEM = """你是台灣政府補助案的資格審查顧問，協助一間公司判斷能否申請某個補助案、以及該怎麼申請。

判斷規則：
- 只依使用者提供的補助案資料與公司資料判斷；公司資料標「未確認」的事項不要自行假設。
- hard_fails 只列補助案資料有明文依據、且公司資料明確不符的條件，每條註明依據。
- 需要查證的文件、證照、身分，或資料不足之處，列入 uncertainties。
- strategic_notes：200 字內，具體說明這間公司要怎麼準備與送件。
- recommended_angle：建議的計畫題目切角，說明如何連結公司本業讓審查委員認可。
- 一律使用繁體中文。"""


class _LLMJudgment(BaseModel):
    verdict: Literal["eligible", "likely", "conditional", "ineligible"]
    hard_fails: list[str]
    uncertainties: list[str]
    strategic_notes: str
    recommended_angle: str


class LLMMatchError(RuntimeError):
    """Claude 沒有回傳可用的判定（拒答或沒有結構化輸出）。"""


def _yes_no(value: bool | None) -> str:
    if value is None:
        return "未確認"
    return "是" if value else "否"


def _llm_prompt(program: SubsidyProgram, profile: CompanyProfile, today: date) -> str:
    insured = profile.insured_employees if profile.insured_employees is not None else "未確認"
    owner_age = profile.owner_age if profile.owner_age is not None else "未確認"
    return f"""## 補助案
- 名稱：{program.name}
- 主辦機關：{program.agency}
- 類別：{program.category}
- 狀態：{program.status}
- 申請期間：{program.window_open or "未載明"} ～ {program.window_close or "常態受理"}
- 最高金額：{program.max_amount_twd or "未載明"}（補助比率：{program.subsidy_rate or "未載明"}）
- 金額說明：{program.amount_notes or "無"}
- 結構化資格條件：{program.eligibility.model_dump_json()}
- 申請方式：{program.how_to_apply or "未載明"}
- 應備文件：{"、".join(program.required_documents) or "未載明"}
- 備註：{program.notes or "無"}

## 公司
- 名稱：{profile.company_name}
- 行業：{profile.industry_desc}（主計總處行業代碼 {profile.industry_code}）
- 員工數：{profile.employee_count}（勞保投保 {insured} 人）
- 實收資本額：{profile.capital_twd:,} 元
- 設立日期：{profile.founded}
- 所在地：{profile.region}
- 負責人年齡：{owner_age}
- 已完成 20 小時創業課程：{_yes_no(profile.owner_has_20h_startup_course)}
- 有公司或商業登記：{_yes_no(profile.is_company_registered)}
- 有欠稅：{_yes_no(profile.has_tax_debt)}
- 有勞動法規違規紀錄：{_yes_no(profile.has_labor_law_violations)}
- 曾獲補助：{"、".join(profile.previous_grants) or "無"}
- 數位工具：{"、".join(profile.digital_tools) or "未提供"}

今天日期：{today}"""


def _merge_llm(heuristic: MatchResult, judgment: _LLMJudgment) -> MatchResult:
    flagged = [f"AI 判讀可能不符：{f}" for f in judgment.hard_fails]
    if judgment.verdict == "ineligible" and not flagged:
        flagged = ["AI 判讀為不符但未列依據，請人工確認"]

    if heuristic.verdict not in _ACTIONABLE_RANK:
        verdict = heuristic.verdict
    elif flagged:
        verdict = Verdict.conditional
    else:
        verdict = max(
            heuristic.verdict, Verdict(judgment.verdict), key=_ACTIONABLE_RANK.__getitem__
        )

    uncertainties = list(
        dict.fromkeys(
            heuristic.uncertainties + flagged + [f"AI：{u}" for u in judgment.uncertainties]
        )
    )
    return heuristic.model_copy(
        update={
            "verdict": verdict,
            "uncertainties": uncertainties,
            "reasoning": _reasoning(heuristic.passes, heuristic.hard_fails, uncertainties)
            + "（AI 精煉）",
            "strategic_notes": judgment.strategic_notes,
            "recommended_angle": judgment.recommended_angle,
        }
    )


async def llm_match_one(
    program: SubsidyProgram,
    profile: CompanyProfile,
    client: anthropic.AsyncAnthropic,
    today: date | None = None,
) -> MatchResult:
    """規則配對 + Claude 深度判讀，回傳合併後的 MatchResult（含申請策略）。"""
    today = today or taipei_today()
    heuristic = match_one(program, profile, today=today)
    resp = await client.beta.messages.parse(
        model=os.environ.get("SUBSIDY_RADAR_MODEL", _LLM_MODEL_DEFAULT),
        max_tokens=16000,
        system=_LLM_MATCH_SYSTEM,
        messages=[{"role": "user", "content": _llm_prompt(program, profile, today)}],
        output_format=_LLMJudgment,
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise LLMMatchError(f"{program.id}: stop_reason={resp.stop_reason}")
    return _merge_llm(heuristic, resp.parsed_output)


def _is_transient(exc: anthropic.APIStatusError) -> bool:
    return exc.status_code == 429 or exc.status_code >= 500


async def llm_refine_all(
    programs: list[SubsidyProgram],
    matches: list[MatchResult],
    profile: CompanyProfile,
    today: date | None = None,
    client: anthropic.AsyncAnthropic | None = None,
    max_concurrency: int = 4,
) -> list[MatchResult]:
    """只精煉可行動案件；單案暫時性失敗保留規則結果，認證或參數錯誤直接拋出。"""
    llm = client or anthropic.AsyncAnthropic()
    prog_map = {p.id: p for p in programs}
    sem = asyncio.Semaphore(max_concurrency)

    async def refine(m: MatchResult) -> MatchResult:
        program = prog_map.get(m.program_id)
        if program is None or m.verdict not in _ACTIONABLE_RANK:
            return m
        async with sem:
            try:
                return await llm_match_one(program, profile, llm, today=today)
            except anthropic.APIStatusError as exc:
                if not _is_transient(exc):
                    raise
                error = type(exc).__name__
            except (LLMMatchError, ValidationError, anthropic.APIConnectionError) as exc:
                error = type(exc).__name__
        logger.warning(
            "subsidy.llm_match_failed", extra={"program_id": m.program_id, "error": error}
        )
        return m

    refined = await asyncio.gather(*(refine(m) for m in matches))
    return sorted(refined, key=lambda r: (-r.score, r.program_name))
