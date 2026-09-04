# 20 — 政府補助雷達 (subsidy_radar)

> 「把 2026 年所有能領的補助都找出來、蒸餾成結構化資料、配對資格、產出行動包。」

## 一句話

`subsidy_radar` 是獨立的 Python 套件，跑「抓取 → 蒸餾 → 配對 → 行動包 → 報告」
一條 pipeline，輸出一份 Markdown 雷達報告告訴 Ivan 哪些補助可以辦、怎麼辦、什麼時候辦。

## 架構

```
data/subsidies/
├── seed_2026.json          ← 已蒸餾好的 10 項補助案（離線跑不需網路）
├── company_profile.yaml    ← 公司側寫（配對引擎的輸入）
├── sources.yaml            ← 來源登記簿（fetch 模組讀取）
├── raw/                    ← httpx 抓下來的 HTML 快照（gitignored）
└── inbox/                  ← 瀏覽器另存新檔的頁面（手動丟進來，gitignored）

subsidy_radar/
├── __init__.py
├── __main__.py             ← python -m subsidy_radar
├── models.py               ← Pydantic 資料契約（金額 Decimal、frozen）
├── fetch.py                ← httpx 抓取 + inbox 攝取
├── extract.py              ← HTML → 純文字（stdlib html.parser）
├── distill.py              ← heuristic + LLM 蒸餾成 SubsidyProgram
├── match.py                ← 資格硬門檻 + 評分 → MatchResult
├── plan.py                 ← 行動包產生器 → ActionPlan
├── pipeline.py             ← 串接各模組的 orchestrator
├── report.py               ← RadarReport → Markdown
└── cli.py                  ← typer CLI（scan / list / match）

docs/subsidies/
└── RADAR_2026.md           ← pipeline 輸出的雷達報告
```

## 快速開始

```bash
# 離線模式（免網路、免 API key）
make subsidy-scan

# 或直接用 CLI
python -m subsidy_radar scan

# 只看配對結果
make subsidy-match

# 列出 seed 裡的補助案
make subsidy-list
```

## 來源管理

### 自動抓取（httpx）
`data/subsidies/sources.yaml` 裡有 URL 的來源，fetch 模組會用 httpx
帶瀏覽器 headers 抓取。失敗（403 / timeout）的來源不影響其他案件。

### Inbox 手動匯入
反爬嚴格的來源（如 arther.talentelevate.co）走 inbox 路線：
1. 在瀏覽器開啟頁面
2. 另存新檔（HTML 或純文字）
3. 丟進 `data/subsidies/inbox/`
4. 重跑 `make subsidy-scan`

### 新增補助案
1. 在 `data/subsidies/seed_2026.json` 加一筆（照既有格式）
2. 或在 `sources.yaml` 加 URL → 用 LLM 蒸餾：
   ```bash
   python -m subsidy_radar scan --llm  # 需 ANTHROPIC_API_KEY
   ```

## 公司側寫

`data/subsidies/company_profile.yaml` 是配對引擎的另一半輸入。
assumptions 欄位列出推測值，報告會標警語。

**送件前必須由負責人逐項確認。**

## 配對引擎

門檻三級：
- **hard_fail** — 一票否決（資本額 / 員工數 / 行業別 / 設立年限不符）
- **uncertainty** — 缺資料或需線下文件確認
- **pass** — 確認通過

判定結果：
- ✅ eligible — 符合，立即可辦
- 🟡 likely — 大致符合，少量待確認
- 🟠 conditional — 有條件（缺關鍵資料）
- 👀 watch — 窗口已截止，追蹤下一輪
- ❌ ineligible — 硬門檻不符

## 行動包

每個可行動案件產出一份行動包，包含：
- 步驟清單（誰做什麼）
- 應備文件
- 截止日 + 緊急度
- AI 代擬申請書草稿（需人工覆核）

## 設計決策

| 決定 | 原因 |
|---|---|
| 金額用 Decimal | 承 CLAUDE.md 不變法則 |
| 離線優先 | 政府網站常擋爬蟲；seed JSON 永遠可跑 |
| heuristic 先於 LLM | 省 API 費用；LLM 只在信心低時才上 |
| inbox 路線 | 行銷頁 / 反爬頁面的務實解法 |
| 模型 frozen | 同專案慣例 |

## 免責聲明

此工具產出的報告僅供內部參考。正式申請前請以各主辦機關官方公告為準。
AI 蒸餾 / 配對結果可能有誤，所有金額與資格條件必須由負責人核實。
