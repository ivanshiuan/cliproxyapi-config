"""申請書代擬 — Claude 依補助案、公司資料與配對結果寫出申請文件草稿。

草稿只是起點：資料沒有的事實一律留【待補】，送件前必須人工逐字覆核。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import anthropic

from subsidy_radar.match import case_context, match_one
from subsidy_radar.models import (
    TAIPEI_TZ,
    CompanyProfile,
    MatchResult,
    SubsidyProgram,
    llm_model,
    taipei_today,
)

_DRAFT_SYSTEM = """你是台灣政府補助案的申請書撰寫顧問，替一間公司代擬申請文件草稿，交給負責人覆核後送件。

撰寫規則：
- 只使用提供的補助案與公司資料。資料沒有的數字、日期、人名、統編、金額、績效指標，一律寫成【待補：需要什麼】，絕對不要自行編造。
- 依補助類型寫最相關的文件：研發或創新類寫計畫書（計畫緣起、現況與問題、計畫目標、執行方法與時程、預期效益、經費需求）；人才培訓類寫訓練計畫；僱用或薪資類寫僱用計畫；貸款類寫資金用途與還款規劃；其他類型寫申請說明書。
- 內容要扣緊公司本業與實際營運，寫出審查委員看得懂的具體做法，避免空泛口號。
- 最後附兩節：「申請表常見欄位建議填法」與「送件前檢查清單」（含應備文件與「規則判定」裡的待確認事項）。
- 申請格式以主辦機關最新公告為準；資料沒提到官方表格時寫通用版並註明。
- 全文使用繁體中文與 Markdown，直接從內容開始，不要寒暄。"""


class DraftError(RuntimeError):
    """Claude 沒有產出可用的草稿（拒答、被截斷或沒有文字）。"""


def _match_context(match: MatchResult) -> str:
    lines = ["", "## 規則判定", f"- 判定：{match.verdict}"]
    lines += [f"- 通過：{p}" for p in match.passes]
    lines += [f"- 待確認：{u}" for u in match.uncertainties]
    return "\n".join(lines)


async def draft_application(
    program: SubsidyProgram,
    profile: CompanyProfile,
    client: anthropic.AsyncAnthropic,
    today: date | None = None,
) -> str:
    """回傳草稿正文（Markdown）。輸出長，用串流避免 HTTP 逾時。"""
    today = today or taipei_today()
    prompt = case_context(program, profile, today) + _match_context(
        match_one(program, profile, today=today)
    )
    async with client.beta.messages.stream(
        model=llm_model(),
        max_tokens=64000,
        system=_DRAFT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        message = await stream.get_final_message()
    if message.stop_reason in ("refusal", "max_tokens"):
        raise DraftError(f"{program.id}: stop_reason={message.stop_reason}")
    body = "".join(block.text for block in message.content if block.type == "text").strip()
    if not body:
        raise DraftError(f"{program.id}: 沒有產出文字")
    return body


def render_draft(
    program: SubsidyProgram, body: str, model: str, generated_at: datetime
) -> str:
    ts = generated_at.astimezone(ZoneInfo(TAIPEI_TZ)).strftime("%Y-%m-%d %H:%M")
    return (
        f"# 申請書草稿：{program.name}\n\n"
        f"> 主辦機關：{program.agency}｜產出：{ts}（台北）｜模型：{model}\n"
        "> ⚠️ AI 代擬草稿，送件前必須逐字覆核；【待補】處要填入真實資料，"
        "格式以主辦機關最新公告為準。\n\n"
        f"{body}\n"
    )
