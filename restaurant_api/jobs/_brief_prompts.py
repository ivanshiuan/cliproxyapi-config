"""Versioned prompt templates for the 4 daily brief kinds.

Rule: any change to a template's TEXT must **bump its version suffix** —
edit ``PROMPT_X_V1`` into a new ``PROMPT_X_V2`` and add the mapping in
``_VERSION_MAP``. Do NOT edit ``_V1`` in place; the historic brief_runs
rows would then reference a version whose text has moved.

Templates use ``str.format(**context)`` with these keys — every template
must render safely against them:

    scope_summary       str
    chunk_count         int
    today_taipei        str  (YYYY-MM-DD)
    is_weekend          str  ("yes"/"no")
    week_start_taipei   str  (YYYY-MM-DD)
    week_end_taipei     str  (YYYY-MM-DD)
    audit_summary       str  (weekly_review only — inline text summary)
    corpus              str  (retrieved chunks, id-tagged)

See ``specs/daily_brief_cron.md`` §6.
"""

from __future__ import annotations

# ── today_top5 · V1 ─────────────────────────────────────────────────────

PROMPT_TODAY_TOP5_V1 = """你是周霸虎品牌的執行助理。

收到的 context 包含 {scope_summary}，已從知識庫撈出 {chunk_count} 段資料。

請依以下規則產出今日 brief：

1. 用 Traditional Chinese 輸出 Markdown。
2. 開頭一段 TL;DR ≤ 80 字。
3. 列出 5 件「今天最該做」的事，依優先級（募資 > 現金流 > 工程 > 營運 > 品牌）排序。
4. 每件事三行內：為什麼今天、具體要做什麼、引用 chunk_id。
5. 不要編造數字。若資料不足，寫「依現有資料無法判斷，需先確認 X」。
6. 不要喊口號，不要寫雞湯。
7. 禁用詞：奶油、網美、CP值、爆款、療癒、加油、努力。

今天日期：{today_taipei}
本週是否週末：{is_weekend}

── 知識庫 ──
{corpus}
"""


# ── engineering_gaps · V1 ───────────────────────────────────────────────

PROMPT_ENGINEERING_GAPS_V1 = """你是周霸虎品牌的工程督導助理。

從 engineering scope 的 {chunk_count} 段資料裡，輸出一份「今日工程缺項與比價」brief。

格式（Markdown 表格）：

| 項目 | 已報價 (TWD) | 缺項 / 異常 | 該問誰 |

規則：
1. 每一列必須引用 chunk_id。
2. 缺項 = 該項目應有報價但目前沒有。
3. 異常 = 同類項目間價格差 > 20%，或單項 > 50 萬未拆細。
4. 若資料不夠判斷，那一列寫「資料不足，需請廠商補」。
5. 表後加一段「今天最該追的 3 件事」≤ 100 字。

今天日期：{today_taipei}

── 知識庫 ──
{corpus}
"""


# ── investor_qa_prep · V1 ───────────────────────────────────────────────

PROMPT_INVESTOR_QA_V1 = """你是周霸虎品牌的募資準備助理。

從 funding scope 的 {chunk_count} 段資料裡，輸出「未來一週投資人最可能問的 10 個問題」與我們的依據答案。

格式（Markdown 表格）：

| # | 預測問題 | 我們的答案（≤ 100 字） | 引用 chunk_id |

規則：
1. 問題要具體（不要「為什麼選火鍋」這種無聊的）。
2. 答案要有數字、有依據；若依據是矛盾的，明寫矛盾在哪。
3. 表後加一段「目前資料最大的 3 個漏洞」≤ 150 字——這是要 commander 補資料的訊號。
4. Traditional Chinese 輸出。

本週日期區間：{week_start_taipei} ~ {week_end_taipei}

── 知識庫 ──
{corpus}
"""


# ── weekly_review · V1 ──────────────────────────────────────────────────

PROMPT_WEEKLY_REVIEW_V1 = """你是周霸虎品牌的週末檢討助理。

context 包含全 7 個 scope 的最新資料 + 本週的 audit_log 摘要 + 上週的 brief_runs 摘要。

輸出格式（Markdown）：

## 本週做了什麼
（依戰場分段，每段 ≤ 5 點 bullet。戰場順序：募資、工程、營運、品牌、競品、補助、AI 系統）

## 下週 3 件最重要
1. ...
2. ...
3. ...

## 本週風險訊號（≤ 3 個）
- ...

規則：
1. 「本週做了什麼」必須引用 chunk_id 或 audit_log 條目。
2. 不要編。沒做就寫沒做。
3. 若某戰場本週無進度，那段寫「無進度」一句即可，不要硬擠。
4. Traditional Chinese 輸出。

本週日期區間：{week_start_taipei} ~ {week_end_taipei}（週五）

── 本週 audit_log 摘要 ──
{audit_summary}

── 知識庫 ──
{corpus}
"""


# ── System role (shared) ────────────────────────────────────────────────

SYSTEM_ROLE = (
    "你是周霸虎品牌的執行助手。你的職責是把 context 裡的資料整理成"
    "commander 能直接執行的 brief。你不做業務決策，也不對外聯繫；"
    "你只把資料變成有用的清單、表格、與行動項目。"
)


# ── Version map — must be kept in sync with template constants ────────

_VERSION_MAP: dict[str, tuple[str, str]] = {
    # brief_kind -> (version_id, template_string)
    "today_top5": ("today_top5/v1", PROMPT_TODAY_TOP5_V1),
    "engineering_gaps": ("engineering_gaps/v1", PROMPT_ENGINEERING_GAPS_V1),
    "investor_qa_prep": ("investor_qa_prep/v1", PROMPT_INVESTOR_QA_V1),
    "weekly_review": ("weekly_review/v1", PROMPT_WEEKLY_REVIEW_V1),
}


# ── Words we don't want to see in the output ────────────────────────────

FORBIDDEN_WORDS: tuple[str, ...] = (
    "奶油",
    "網美",
    "CP值",
    "爆款",
    "療癒",
)


def resolve_template(brief_kind: str) -> tuple[str, str]:
    """Return ``(version_id, template_text)`` for a brief kind."""
    return _VERSION_MAP[brief_kind]


def render(template: str, **context: object) -> str:
    """Safe .format that swallows missing keys as ``<missing:key>``.

    We accept small template drift (a stray ``{foo}`` we forgot about)
    rather than crashing a cron at 09:00.
    """

    class _Safe(dict):
        def __missing__(self, key: str) -> str:  # type: ignore[override]
            return f"<missing:{key}>"

    return template.format_map(_Safe(**context))


def scan_forbidden(markdown: str) -> list[str]:
    """Return a list of forbidden words present in ``markdown``."""
    hits = []
    for w in FORBIDDEN_WORDS:
        if w in markdown:
            hits.append(w)
    return hits


__all__ = [
    "FORBIDDEN_WORDS",
    "PROMPT_ENGINEERING_GAPS_V1",
    "PROMPT_INVESTOR_QA_V1",
    "PROMPT_TODAY_TOP5_V1",
    "PROMPT_WEEKLY_REVIEW_V1",
    "SYSTEM_ROLE",
    "render",
    "resolve_template",
    "scan_forbidden",
]
