"""subsidy_radar — 政府補助雷達.

抓取 → 蒸餾 → 資格配對 → 申請行動包，一條 pipeline 把 2026 年
政府補助案變成可執行的申請清單。

模組地圖：
- models   — Pydantic 資料契約（金額一律 Decimal）
- sources  — 來源登記簿（sources.yaml）
- fetch    — httpx 抓取 + 本地 inbox 攝取
- extract  — HTML → 純文字
- distill  — LLM / heuristic 蒸餾成結構化補助卡
- match    — 資格硬門檻 + 評分
- plan     — 每案申請行動包
- report   — Markdown 雷達報告
- cli      — typer 入口（`python -m subsidy_radar` / `subsidy`）
"""

__version__ = "0.1.0"
