"""蒸餾層 — 把原始文字變成結構化 SubsidyProgram 卡片。

兩條路徑：
1. heuristic_distill()  — 免 LLM；靠正規表達式 + 關鍵字 → 粗結構
2. llm_distill()        — 餵 Anthropic Claude → 精準結構（需 ANTHROPIC_API_KEY）

優先跑 heuristic，LLM 只用於 heuristic 信心低 or 使用者要求 --llm。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from datetime import date
from decimal import Decimal

from subsidy_radar.models import (
    DataConfidence,
    EligibilityRule,
    ProgramCategory,
    ProgramStatus,
    SubsidyProgram,
)

_AMOUNT_RE = re.compile(
    r"(?:新臺幣|NT\$?|TWD)\s*([0-9,]+(?:\.\d+)?)\s*(?:萬|萬元)?(?:元)?|"
    r"(?:補助\s*(?:上限|金額)?|最高)\s*(?:新臺幣|NT\$?)?\s*([0-9,]+(?:\.\d+)?)\s*(?:萬|萬元)?",
    re.IGNORECASE,
)
_PCT_RE = re.compile(r"(\d{1,3})\s*[%％]")
_DATE_RE = re.compile(r"(\d{2,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_CATEGORY_HINTS: dict[str, ProgramCategory] = {
    "貸款": ProgramCategory.loan,
    "利息補貼": ProgramCategory.loan,
    "僱用獎助": ProgramCategory.wage_subsidy,
    "薪資": ProgramCategory.wage_subsidy,
    "訓練": ProgramCategory.training,
    "人力提升": ProgramCategory.training,
    "充電": ProgramCategory.training,
    "點數": ProgramCategory.voucher,
    "雲市集": ProgramCategory.voucher,
    "節能": ProgramCategory.energy,
    "汰換": ProgramCategory.energy,
    "冷氣": ProgramCategory.energy,
    "冰箱": ProgramCategory.energy,
}

_STATUS_HINTS: dict[str, ProgramStatus] = {
    "隨到隨審": ProgramStatus.rolling,
    "隨到隨受理": ProgramStatus.rolling,
    "常態受理": ProgramStatus.rolling,
    "經費用罄": ProgramStatus.closed,
    "已截止": ProgramStatus.closed,
    "不辦理": ProgramStatus.closed,
    "開放申請": ProgramStatus.open,
}


def _extract_amount(text: str) -> Decimal | None:
    """從文字中抓最大金額（新臺幣），回傳 Decimal（元）。"""
    amounts: list[Decimal] = []
    for m in _AMOUNT_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or "").replace(",", "").strip()
        if not raw:
            continue
        val = Decimal(raw)
        span = m.group(0)
        if "萬" in span:
            val *= 10000
        amounts.append(val)
    return max(amounts) if amounts else None


def _extract_rate(text: str) -> str | None:
    m = _PCT_RE.search(text)
    return f"{m.group(1)}%" if m else None


def _extract_dates(text: str) -> list[date]:
    out: list[date] = []
    for m in _DATE_RE.finditer(text):
        y = int(m.group(1))
        if y < 200:
            y += 1911  # 民國→西元
        if y < 1911:
            continue
        with contextlib.suppress(ValueError):
            out.append(date(y, int(m.group(2)), int(m.group(3))))
    return sorted(out)


def _guess_category(text: str) -> ProgramCategory:
    for keyword, cat in _CATEGORY_HINTS.items():
        if keyword in text:
            return cat
    return ProgramCategory.grant


def _guess_status(text: str) -> ProgramStatus:
    for keyword, status in _STATUS_HINTS.items():
        if keyword in text:
            return status
    return ProgramStatus.unknown


def heuristic_distill(
    source_id: str,
    text: str,
    source_urls: list[str] | None = None,
    today: date | None = None,
) -> SubsidyProgram:
    """靠規則 + 正規表達式從原始文字粗抽一張補助卡。"""
    today = today or date.today()
    name_line = text.split("\n")[0][:80] if text else source_id
    dates = _extract_dates(text)
    window_open = dates[0] if dates else None
    window_close = dates[-1] if len(dates) >= 2 else None

    return SubsidyProgram(
        id=source_id,
        name=name_line.strip(),
        agency="",
        category=_guess_category(text),
        status=_guess_status(text),
        window_open=window_open,
        window_close=window_close,
        max_amount_twd=_extract_amount(text),
        subsidy_rate=_extract_rate(text),
        eligibility=EligibilityRule(),
        source_urls=source_urls or [],
        as_of=today,
        confidence=DataConfidence.heuristic,
    )


_LLM_SYSTEM = """你是台灣政府補助案分析師。使用者會提供一段關於某項補助計畫的原始文字。
請輸出 **單一 JSON 物件**，欄位定義如下（全部不可省略）：
{
  "name":          "計畫全稱",
  "agency":        "主辦機關",
  "category":      "grant|loan|voucher|wage_subsidy|training|energy|other",
  "status":        "open|rolling|closed|upcoming|unknown",
  "window_open":   "YYYY-MM-DD 或 null",
  "window_close":  "YYYY-MM-DD 或 null",
  "max_amount_twd": "最高補助金額（新臺幣元，整數字串）或 null",
  "subsidy_rate":  "如 '50%' 或 '全額負擔' 或 null",
  "amount_notes":  "金額條件說明",
  "eligibility": {
    "min_employees": null,
    "max_employees": null,
    "max_capital_twd": null,
    "industry_scopes": [],
    "allowed_regions": [],
    "owner_age_min": null,
    "owner_age_max": null,
    "company_age_max_years": null,
    "requires": ["清單"],
    "notes": ""
  },
  "how_to_apply":  "簡要申請流程",
  "apply_url":     "官方申請網址或 null",
  "required_documents": ["需備文件清單"],
  "next_round_hint": "下一輪開放時間或 null",
  "notes":         "其他重要事項"
}

不要加 markdown 格式、不要加 ```json```。直接回 JSON 物件。金額一律用「元」為單位的整數字串。"""


async def llm_distill(
    source_id: str,
    text: str,
    source_urls: list[str] | None = None,
    today: date | None = None,
    api_key: str | None = None,
) -> SubsidyProgram:
    """用 Claude 蒸餾一段原始文字成精準的 SubsidyProgram。"""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("需安裝 anthropic SDK：pip install anthropic") from exc

    today = today or date.today()
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 未設定")

    client = anthropic.AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=_LLM_SYSTEM,
        messages=[{"role": "user", "content": text[:12000]}],
    )
    raw_json = resp.content[0].text.strip()
    data = json.loads(raw_json)

    return SubsidyProgram(
        id=source_id,
        name=data.get("name", source_id),
        agency=data.get("agency", ""),
        category=ProgramCategory(data.get("category", "other")),
        status=ProgramStatus(data.get("status", "unknown")),
        window_open=date.fromisoformat(data["window_open"]) if data.get("window_open") else None,
        window_close=(
            date.fromisoformat(data["window_close"]) if data.get("window_close") else None
        ),
        max_amount_twd=data.get("max_amount_twd"),
        subsidy_rate=data.get("subsidy_rate"),
        amount_notes=data.get("amount_notes"),
        eligibility=EligibilityRule(
            min_employees=data.get("eligibility", {}).get("min_employees"),
            max_employees=data.get("eligibility", {}).get("max_employees"),
            max_capital_twd=data.get("eligibility", {}).get("max_capital_twd"),
            industry_scopes=data.get("eligibility", {}).get("industry_scopes", []),
            allowed_regions=data.get("eligibility", {}).get("allowed_regions", []),
            owner_age_min=data.get("eligibility", {}).get("owner_age_min"),
            owner_age_max=data.get("eligibility", {}).get("owner_age_max"),
            company_age_max_years=data.get("eligibility", {}).get("company_age_max_years"),
            requires=data.get("eligibility", {}).get("requires", []),
            notes=data.get("eligibility", {}).get("notes"),
        ),
        how_to_apply=data.get("how_to_apply"),
        apply_url=data.get("apply_url"),
        required_documents=data.get("required_documents", []),
        source_urls=source_urls or [],
        as_of=today,
        confidence=DataConfidence.llm,
        next_round_hint=data.get("next_round_hint"),
        notes=data.get("notes"),
    )
