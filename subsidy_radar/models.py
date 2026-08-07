"""資料契約 — 補助案、公司側寫、配對結果、行動包。

不變法則（承 CLAUDE.md）：
- 金額一律 Decimal（TWD 型別擋 float）
- 輸入模型 frozen
- 時間戳 tz-aware（報告以 Asia/Taipei 呈現）
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

TAIPEI_TZ = "Asia/Taipei"


def _no_float(v: Any) -> Any:
    """金額欄位拒收 float — 用字串或 int，內部一律 Decimal。"""
    if isinstance(v, float):
        raise ValueError("金額不可用 float，請用字串或整數（Decimal 語意）")
    if isinstance(v, str):
        return v.replace(",", "").strip()
    return v


TWD = Annotated[Decimal, BeforeValidator(_no_float)]


class ProgramCategory(StrEnum):
    grant = "grant"  # 直接補助款
    loan = "loan"  # 貸款 / 利息補貼
    voucher = "voucher"  # 點數 / 抵用券
    wage_subsidy = "wage_subsidy"  # 僱用 / 薪資獎助
    training = "training"  # 教育訓練費用
    energy = "energy"  # 節能設備汰換
    other = "other"


class ProgramStatus(StrEnum):
    open = "open"  # 窗口開放中（有明確截止日）
    rolling = "rolling"  # 隨到隨審 / 常態受理
    closed = "closed"  # 本年度已截止
    upcoming = "upcoming"  # 尚未開放
    unknown = "unknown"


class DataConfidence(StrEnum):
    official = "official"  # 官方公告原文
    web_search = "web_search"  # 網路查證（需以官網為準）
    llm = "llm"  # LLM 從網頁蒸餾
    heuristic = "heuristic"  # 規則式粗抽取
    manual = "manual"  # 人工登錄


class Verdict(StrEnum):
    eligible = "eligible"  # 符合，立即可辦
    likely = "likely"  # 大致符合，少量待確認
    conditional = "conditional"  # 有條件（缺關鍵資料或需先補件）
    watch = "watch"  # 資格符合但窗口未開 / 已截止，追蹤下一輪
    ineligible = "ineligible"  # 硬門檻不符
    unknown = "unknown"


class EligibilityRule(BaseModel):
    """結構化資格門檻 — None 代表該計畫無此限制。"""

    model_config = ConfigDict(frozen=True)

    min_employees: int | None = None
    max_employees: int | None = None
    max_capital_twd: TWD | None = None
    # 行業別範圍：["45-96"]（主計總處行業代碼前2碼區間）、["56"]（前綴）、["5610"]（精確）
    industry_scopes: list[str] = Field(default_factory=list)
    allowed_regions: list[str] = Field(default_factory=list)
    owner_age_min: int | None = None
    owner_age_max: int | None = None
    company_age_max_years: int | None = None
    # 無法結構化的硬條件（如「工商憑證」「20 小時創業課程」）— 配對時列為待確認
    requires: list[str] = Field(default_factory=list)
    notes: str | None = None


class SubsidyProgram(BaseModel):
    """一張補助卡 — 蒸餾層的輸出、配對層的輸入。"""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    agency: str
    category: ProgramCategory
    status: ProgramStatus
    window_open: date | None = None
    window_close: date | None = None
    max_amount_twd: TWD | None = None
    subsidy_rate: str | None = None  # 如「50%」「全額負擔」
    amount_notes: str | None = None
    eligibility: EligibilityRule = Field(default_factory=EligibilityRule)
    how_to_apply: str | None = None
    apply_url: str | None = None
    required_documents: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    as_of: date
    confidence: DataConfidence
    next_round_hint: str | None = None
    notes: str | None = None


class CompanyProfile(BaseModel):
    """我方公司側寫 — 配對引擎的另一半輸入。

    assumptions 列出哪些欄位是推測值，報告會標警語，
    送件前必須由負責人逐項確認。
    """

    model_config = ConfigDict(frozen=True)

    company_name: str
    industry_code: str  # 主計總處行業細類，如餐館業 5610
    industry_desc: str
    employee_count: int
    insured_employees: int | None = None  # 勞保投保人數（小人提/大人提以此認定）
    capital_twd: TWD
    founded: date
    region: str
    owner_age: int | None = None
    owner_has_20h_startup_course: bool | None = None
    is_company_registered: bool = True  # 有公司 / 商業登記
    has_tax_debt: bool | None = None
    has_labor_law_violations: bool | None = None
    previous_grants: list[str] = Field(default_factory=list)
    digital_tools: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    """單一補助案 × 公司側寫的判定結果。"""

    model_config = ConfigDict(frozen=True)

    program_id: str
    program_name: str
    verdict: Verdict
    score: int  # 0-100，僅供排序
    passes: list[str] = Field(default_factory=list)
    hard_fails: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    estimated_value_twd: TWD | None = None
    reasoning: str = ""


class ActionStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: int
    title: str
    detail: str = ""
    owner: str = "Ivan"  # Ivan / Claude / 會計師 / 外部顧問


class ActionPlan(BaseModel):
    """一案一包：照著做就能送件。"""

    model_config = ConfigDict(frozen=True)

    program_id: str
    program_name: str
    priority: int  # 1 = 最優先
    deadline: date | None = None
    urgency: str = ""  # 🔴急 / 🟠中 / 🟢常態
    steps: list[ActionStep] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    draft_notes: str | None = None  # LLM 代擬的申請重點草稿（需人工覆核）
    disclaimer: str = ""


class RadarReport(BaseModel):
    """整份雷達輸出 — report.py 由此渲染 Markdown。"""

    model_config = ConfigDict(frozen=True)

    generated_at: datetime  # tz-aware
    profile: CompanyProfile
    programs: list[SubsidyProgram] = Field(default_factory=list)
    matches: list[MatchResult] = Field(default_factory=list)
    plans: list[ActionPlan] = Field(default_factory=list)
