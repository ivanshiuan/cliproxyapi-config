# 18 — 財務分析作戰手冊（Financial Analysis Ops Playbook）

> 把 Anthropic「Claude for Financial Services」那 17 個 plugin，**收編成我們餐飲 OS 自己的一套財務紀律**。
> 這份不是外掛清單，是「我們怎麼**有紀律地**用它把數據變成決策」的 SOP。
> 上位文件：`docs/00_vision.md`（SSOT）、`docs/04_data_schema.md`（事實層）、`docs/08_safety_compliance.md`（覆核文化）。

---

## 零、一句話這是什麼

我們裝的不是「金融業工具」，是一層 **「財務解讀 / 決策層」**，疊在我們既有的**事實層**（`restaurant_api`）之上。

```
生產層   DevSwarm（LangGraph 蜂群）      → 產出程式碼
────────────────────────────────────────────────────
事實層   restaurant_api（25 表 PG）      → 產出「可稽核的真實數字」
         mv_daily_pnl / ledgers / COGS   （真毛利、損耗、工時、點數）
────────────────────────────────────────────────────
解讀層   Claude 財務 skills（本手冊）    → 把數字變成「故事 + 決策 + 簽核文件」
         單位經濟 / 差異解說 / 投組監控 / 展店 IC memo / 估值
```

**核心心法：餐飲本質上是三門財務生意的疊加 —**
1. 一門**每天結算的現金流生意**（日損益、對帳、差異）→ 用 fund-admin / audit 系列
2. 一組**多店資產組合**（連鎖 / 加盟）→ 用 PE 的 portfolio-monitoring / comps
3. 一門**會員 LTV 生意**（點數、回購）→ 用 PE 的 unit-economics

華爾街用這些工具分析上市公司；我們用**同一套嚴謹邏輯**分析「每一家店、每一個會員、每一次展店」。

---

## 一、對我們的好處（為什麼值得有紀律地做）

| # | 好處 | 對應願景缺口（docs/00） |
|---|---|---|
| 1 | **補上「假獲利 → 真獲利」的最後一哩：解讀層。** 我們已經算得出真實淨利，但缺「把數字講成老闆看得懂的決策」的能力。這層就是。 | 「假獲利」缺口 |
| 2 | **給「看結果不看程式碼的指揮官」board-grade 輸出**（xlsx / pptx / 一頁 memo），天然對齊我們的 PR 覆核文化。 | CLAUDE.md PR 工作流 |
| 3 | **展店 / 加盟決策從拍腦袋 → IC memo + IRR/MOIC**，每個決策都可稽核、可回溯。 | 「加盟無管控」缺口 |
| 4 | **會員經營從「發點數」升級成 LTV / CAC / cohort 回購分析**（我們已有 `customer_points_ledger`）。 | 模組五（行銷 ROI）、docs/13 |
| 5 | **用 Anthropic 維護的專業級財務邏輯，不自己重造輪子**——而且它的內建規則（BS 必平、現金 tie-out、金額精度）跟我們「金額 Decimal、可稽核、人工覆核」法則天生同源。 | 不變法則 |
| 6 | **紀律化**：固定節奏 + SOP 檔 + 覆核，把「偶爾玩一下 AI」變成「每月穩定產出的財務例行」。 | docs/14 店務、docs/16 店長 |

---

## 二、能力對照表（skill → 我們的場景 → 吃哪張表）

> 挑著用。這些 skill 預設美股 / GAAP 語境，**只採用邏輯，不套用它的市場假設**。

### A. 立刻能用（貼著我們現有數據）

| Skill / Agent | 我們拿它做什麼 | 輸入資料源（既有） |
|---|---|---|
| `audit-xls` | 稽核任何試算表：公式錯誤、損益不平、現金對不上 | 供應商報價單、盤點表、POS 匯出 |
| `clean-data-xls` | 清洗髒資料：去空白、統一大小寫、文字轉數字、去重 | 廠商發票 CSV、手key 盤點 |
| `unit-economics`（PE） | 會員 LTV / CAC / 回購 cohort / 回本期 / 毛利瀑布 | `customer_points_ledger`、orders |
| `variance-commentary`（fund-admin） | 每條損益 / 資產負債超過門檻就寫差異解說（本期 vs 上期 vs 預算） | `mv_daily_pnl`、`cogs_variance_detector` |
| `gl-recon` + `break-trace`（fund-admin） | 帳對帳：POS vs 銀行 vs 現金，抓 break 並回溯根因 | `audit_log`、`stock_movements`、現金對帳 |
| `roll-forward` / `accrual-schedule`（fund-admin） | 月結：科目結轉、應計（如未開發票、待攤費用） | ledgers |
| `ib-check-deck` | 報告出手前 QC：跨頁數字一致、口徑對齊 | 我們產出的任何 deck |

### B. 決策 / 展店場景

| Skill / Agent | 我們拿它做什麼 | 觸發時機 |
|---|---|---|
| `portfolio-monitoring`（PE） | 多店當一個投組，追每店 vs 計畫、抓落後店 | 加盟總部週 / 月檢視 |
| `comps-analysis` | 店與店互相 benchmark（坪效、人事佔比、食材佔比） | 連鎖橫向比較 |
| `returns-analysis`（PE, IRR/MOIC） | 新店投資報酬敏感度（裝潢攤提、回本期、租金情境） | 展店評估 |
| `ic-memo`（PE） | 「要不要開這家店 / 收這個加盟」寫成投委會備忘錄 | 重大資本決策 |
| `dcf-model` / `3-statement-model` | 整個品牌估值（募資、賣店、引資） | 募資 / 併購事件 |
| `market-researcher` / `competitive-analysis` | 商圈競品地圖、區域消費力 | 選址、展店（對應模組七地圖） |
| `pptx-author` / `xlsx-author` | 無 Office 環境下直接產出 .pptx / .xlsx 給指揮官 | 所有對外產出 |

### C. 我們用不到 / 先不碰
- 資料連接器 `capiq` / `factset` / `daloopa` / `nav` / `internal-gl` 等 → **台灣中小餐飲接不上、要付費帳號**。策略上走**本地匯出**（見第四節），不接華爾街數據源。
- `lseg`、`sp-global`（合作夥伴數據源）、`claude-for-msft-365-install` → 未安裝，短期不需要。
- `kyc-screener`、`buyer-list`、`teaser`、`cim` 等純投行 / 合規流程 → 目前無場景，備而不用。

---

## 三、紀律法則（財務層的「不變法則」）

> 對齊 `CLAUDE.md` 的不變法則，違反就是不合格產出。

1. **事實層唯一真相（SSOT）**：財務 skill 產出的 Excel / PPT **永遠不是真相來源**。所有數字必須能 tie back 到 `restaurant_api` 的 `mv_daily_pnl` / ledger。skill 產出的檔案是**分析視圖，不入帳**。（同 `to_md.py` 純文字不可直接信帳的法則。）
2. **金額永遠 Decimal、時間永遠 tz-aware**：對帳表最後一列必須平（BS balance / 現金 tie-out）。`audit-xls` 內建這些檢查，出手前一定跑。
3. **每份產出都 staged for human review**：財務結論一律**指揮官（Ivan）簽核後才對外**。AI 產出 = 初稿，不是定案。（同 Anthropic 官方 disclaimer，同我們 docs/08 覆核文化。）
4. **來源標註（provenance）**：每份報告頭部必寫「哪個 tenant / store / 期間、抓自哪張表 / 哪次匯出、跑哪個 skill、版本日期」。無來源不得流通。
5. **固定節奏才叫紀律**：日 / 週 / 月 / 季各有**排定**的財務動作（見第五節），不是想到才做。
6. **一個工作流一個 SOP 檔**：穩定的財務流程寫成 `specs/finance_*.md`，像對待 DevSwarm 任務一樣管理，可重跑、可交接。
7. **穩定即產品化**：任何財務工作流跑順、每月都要用 → **反向寫成 DevSwarm spec**，讓蜂群沉澱成 `restaurant_api` 原生報表 / router。skill 是研發沙盒，產品是常規（見第四節 Phase 4）。

---

## 四、整合架構（資料怎麼流）

```
┌─────────────────┐   scripts/export_for_finance.py     ┌──────────────────┐
│ restaurant_api  │   （scope 到 tenant/store/期間，     │  乾淨 xlsx / csv  │
│  Postgres       │──▶ 產出 skill 吃得下的乾淨檔）      ─▶│ finance_inbox/   │
│  mv_daily_pnl   │                                       └────────┬─────────┘
│  ledgers        │                                                │
└─────────────────┘                                                ▼
                                                        ┌────────────────────┐
      docs/finance_reports/  ◀── QC ──┐                 │  財務 skill         │
      （.xlsx/.pptx/.md 產出）         │                 │  unit-economics /   │
              │                        │                 │  variance /         │
              ▼                        │                 │  portfolio / ic-memo│
      指揮官覆核簽核 ◀── ib-check-deck ─┘◀───────────────└────────────────────┘
                          audit-xls
```

**約定目錄（Phase 0 建立）：**
- `scripts/export_for_finance.py` — 從 PG 匯出乾淨資料（scope、Decimal 保留、UTC→Taipei）
- `finance_inbox/` — skill 的輸入檔（gitignored，臨時）
- `docs/finance_reports/` — 正式產出（可留存、可簽核）
- `specs/finance_*.md` — 穩定財務工作流的 SOP

---

## 五、固定節奏（Cadence — 這就是「有紀律」）

| 頻率 | 動作 | 主要 skill | 產出 |
|---|---|---|---|
| **日** | 現金 / POS 對帳，抓 break | `gl-recon` + `break-trace` | 對帳表（異常才升級） |
| **週** | 多店表現 vs 計畫，抓落後店 | `portfolio-monitoring` | 一頁週報 |
| **月** | 月結：損益覆盤 + 差異解說 + 單位經濟 | `variance-commentary` + `unit-economics` + `audit-xls` | 月度財務包（.xlsx + 一頁 md） |
| **季** | 會員 LTV / cohort 回購覆盤、坪效 comps | `unit-economics` + `comps-analysis` | 季度會員 & 店效報告 |
| **事件觸發** | 展店 / 加盟 / 募資決策 | `returns-analysis` + `ic-memo` + `dcf` | IC memo + IRR/MOIC 表 |

---

## 六、分階段落地計畫（規劃到完整）

### Phase 0 — 規範與骨架（本週，零風險，不碰外部數據）
- [x] 寫本手冊（docs/18）
- [ ] 建目錄：`finance_inbox/`（加 .gitignore）、`docs/finance_reports/`
- [ ] 寫 `scripts/export_for_finance.py` 雛形：輸入 `--tenant --store --period`，輸出乾淨 xlsx（Decimal 保留、UTC→Taipei、只 scope 指定店）
- [ ] 挑**一個**示範工作流跑通（建議：單店月度損益覆盤）
- **驗收**：一條指令從 PG 匯出 → skill 產出 → `audit-xls` 通過 → 一頁 md 報告落在 `docs/finance_reports/`

### Phase 1 — 單店財務作戰室（最快見效）
把單店 `mv_daily_pnl` 變成「月度損益覆盤 + 差異解說 + 單位經濟」一頁報告。
- 用 `variance-commentary` 解讀每條超門檻的損益變化
- 用 `unit-economics` 算該店客單、回購、食材 / 人事佔比
- 用 `audit-xls` 把關數字
- **貼著我們既有的 `profit_calc.md` / `cogs_variance_detector.md`，幾乎零學習成本**

### Phase 2 — 多店投組監控（加盟總部場景）
- `portfolio-monitoring`：多店當投組，統一 KPI、抓落後
- `comps-analysis`：店間坪效 / 佔比 benchmark，找 best / worst practice
- **驗收**：一張多店紅綠燈儀表板 + 落後店根因清單

### Phase 3 — 展店 / 募資決策（高單價決策）
- `returns-analysis`：新店 IRR / MOIC 敏感度（租金、裝潢攤提、回本期情境）
- `ic-memo`：把「開不開這家店」寫成投委會備忘錄
- `dcf` / `3-statement-model`：品牌整體估值（募資 / 賣店時）
- **驗收**：一份可直接進董事會 / 投資人的 IC memo

### Phase 4 — 產品化（把紀律沉澱進系統）
把 Phase 1–3 中**每月固定要跑**的工作流，**反向寫成 DevSwarm spec**，讓蜂群把它變成 `restaurant_api` 的原生報表 / router。
- 例：「月度損益覆盤」穩定後 → `specs/finance_monthly_review_router.md` → 蜂群產出 `/reports/monthly-review` API
- **從此 skill 只做探索 / 一次性分析，常規報表由產品自動出**——這才是把外掛真正「變成我們自己的東西」

---

## 七、風險與界線（誠實說）

- **市場假設不套用**：這些 skill 預設美股 / SEC filing / trading multiples。台灣中小餐飲用不到，**只借邏輯**。
- **產出是分析視圖不是帳**：紀律第 1 條，任何入帳金額仍走 `restaurant_api` 結構化驗證。
- **資料連接器多用不到且要付費**：走本地匯出策略，不接外部源。
- **啟用生效**：slash command（如 `/comps`、`/ic-memo`）與 agent 需**重開 session** 才出現；skill 本 session 已可自動觸發。
- **安裝位置**：plugin 裝在 Claude Code **user 層級**，與本 repo 隔離，不影響專案檔。

---

## 八、立即下一步（建議）

跑 **Phase 0 示範**：建目錄骨架 + 寫 `export_for_finance.py` 雛形 + 用一家 seed 店的資料，產出第一份「單店月度損益覆盤」一頁報告，走完「匯出 → skill → audit → 簽核」全鏈路。

跑通這一個，整套紀律就從紙上變成肌肉記憶。
