# 12 — Spec Discipline Playbook

> 為什麼有這份文件：從 `openai/gpt-5-coding-examples` 偷來的 YAML-spec + 模型並排對照
> 兩個模式，已經在 PR #10 落地成 `scripts/validate_spec.py` + `scripts/bakeoff.py`。
> 這份文件把「工具」升級成「紀律」— 讓下次開 session 的 Claude（與 Ivan 本人）
> 都自動記得完整用法、節奏、與例外規則。
>
> 讀完這篇你會知道：**每個新 module 從想法到 `implemented`，該走哪 10 步**。

---

## 一句話總綱

**Spec 是合約，不是說明文件。** 每個 spec 在被 swarm 執行之前，必須經過三道 gate：
`validator 通過 → bakeoff 定型 model → swarm 自動套 budget + model`。

這把 AI 燒錢生產程式碼的隨機性，變成工程學上可預測的產線。

---

## 這套流程對我們有什麼好處

| 面向 | 沒紀律的樣子 | 有紀律之後 |
|---|---|---|
| **成本** | 每個 spec 都用 Opus，月 $80-100 | bakeoff 定型 → 真需要才 Opus，月 $15-25，省 70% |
| **命中率** | Coder 拿到模糊 spec → 亂寫 → 燒 3 次 heal → 失敗 | validator 擋 underspec → 每次 swarm 都有 ROI |
| **可稽核** | 「這個 module 誰產的？什麼模型？花多少？」查不到 | frontmatter + workspace 有完整審計線索 |
| **知識傳承** | 新 spec 靠人肉抄 `profit_calc.md` | 模板 + validator + agent 一起強制對齊 |
| **規模化** | spec 從 10 個變 30 個 → 品質崩盤 | 線性成長，30 個 spec 的月成本可預測 |

**最大的隱形收益**：Ivan 花在 review AI 產出的時間下降。因為 spec 品質可控
→ Coder 產出可控 → 只在 promote 前那一關花 5 分鐘看 diff，不用每次都回頭改 spec 再跑一次。

---

## 紀律怎麼守 — 機制 vs 習慣

紀律不靠意志力，靠設計。分兩層：**機制**（自動化，跑不掉）+ **習慣**（人的反射）。

### 已就位的機制（PR #10）

- ✅ `scripts/validate_spec.py` — 檢查 frontmatter + sections + ≥10 ACs + body 長度
- ✅ `/swarm SPEC=…` 拒絕跑未驗證 spec（`make swarm SPEC=…` 亦然）
- ✅ `make full-check` 包含 `spec-check`
- ✅ frontmatter 自動讀 `budget_usd` + `preferred_model`，`/swarm` 自動套 `DEVSWARM_MODEL_CODER`
- ✅ `/spec` slash command + `spec-writer` agent 都會在寫完後跑 validator，不綠不 return

### 建議追加的機制（未做，做了更爽）

排優先度：

1. **pre-commit hook**（20 分鐘）— commit 動到 `specs/*.md` 就強制跑 validator
2. **GitHub Action**（30 分鐘）— PR 觸發 spec-check 並在 PR comment 差異
3. **Cost dashboard**（2 小時）— 每月/每 spec 累積花費，用 `workspace/*/task.json`
4. **Bakeoff auto-PR**（3 小時）— bakeoff 跑完發現可降級 → 自動開 PR 改 frontmatter
5. **Spec status → DB**（半天）— `backlog.py` 從 filesystem grep 升級 SQL

### 人的四條硬規（習慣）

1. **寫 spec 一律 `/spec`**，不手寫 markdown
2. **跑 swarm 一律 `/swarm SPEC=…`**，不用 inline `REQ=`（除非 prototype）
3. **新 spec 首次跑先 `/bakeoff`**，定型後才 `/swarm`
4. **每月最後一個週五**：抽 3 個 `implemented` spec 重跑 `/bakeoff`，看有沒有降級空間或 model regression

---

## 完整工作流 — 一個 Spec 的一生（10 步）

```
0. 想法               白板/口頭理清「什麼 in / 什麼 out」
    ↓
1. /spec <name>       spec-writer agent 產 draft，跑完 validator 才 return
    ↓                 status: draft
2. 人審 5-10 分鐘      改 AC 加具體數字、補 out-of-scope、確認 kind 正確
    ↓                 status: draft → ready
3. /bakeoff           首次跑 opus/sonnet/haiku 三模型，看哪個能過
    ↓
4. 更新 preferred_model   改 frontmatter 為最便宜的通過模型
    ↓
5. /swarm SPEC=…      真正執行；讀 spec 的 budget + model 自動套
    ↓
6. make promote TASK=…    workspace/<task>/ → restaurant_api/
    ↓
7. make full-check    ruff + pyright + pytest + db-smoke + spec-check 全綠
    ↓                 status: ready → implemented
8. 自動開 PR          per CLAUDE.md 慣例（不用問 Ivan）
    ↓
9. Ivan review + merge
    ↓
10. 一個月後          隨機抽樣重跑 /bakeoff（regression 檢查）
```

**每步的失敗處理**：

| 步 | 失敗症狀 | 補救 |
|---|---|---|
| 1 | validator 不綠 | agent 已自我修正；若三次還不綠 → 停下來人審 |
| 3 | 三個模型都掛 | spec 本身有問題，回步 2 |
| 3 | 只有 opus 綠 | `preferred_model: opus` 定型，接受高成本 |
| 5 | budget halted | 看 QA root cause；spec 收緊 out-of-scope 或加 heal iter |
| 5 | heal exhausted | 讀 `workspace/<task>/task.json` 的 qa_report 決定改 spec 還是升級模型 |
| 6 | promote 有衝突 | 手動 merge，然後補測試 |
| 7 | pytest fail | 走 `/bugfix` 或 Ivan 親自處理 |

---

## 節奏建議

| 頻率 | 動作 | 時間 |
|---|---|---|
| **每個新功能** | 走完 step 0-9 | ~30 分鐘 human + 15 分鐘 AI |
| **每週五下班前** | `make spec-check` + `make backlog` 看有無 draft/ready 卡住 | 3 分鐘 |
| **每月最後週五** | 抽 3 個 `implemented` spec 跑 `/bakeoff` regression check | 20 分鐘 |
| **每季** | review 本 playbook + `docs/06_execution_plan.md`，看策略要不要更新 | 30 分鐘 |

---

## 例外 — 什麼時候不走這套

不是每件事都要 spec。判準：**這件事會不會產生一個新 module？**
會 → 走全套；不會 → 跳過。

- **緊急 hotfix**：直接改 `restaurant_api/`，事後補 spec 當 post-mortem
- **prototype 驗證**：用 `/swarm REQ="…"` inline 快跑，有價值再回頭補 spec
- **既有 code 重構**：spec 沒意義，直接改 + 補測試
- **文件更新**：`docs/*.md` 不需 spec
- **依賴升級**：不需 spec，跑 `make full-check` 即可

---

## Spec 的 kind 選擇

Frontmatter 的 `kind` 欄有兩種，各自對應不同的必要 section 清單。

### `kind: pure-function`

用在**單一模組、單一 Python 檔、無 I/O** 的計算或驗證。範例：
`profit_calc`, `discount_resolver`, `uniform_invoice_validator`, `bom_consumer`,
`cogs_variance_detector`, `labor_hours_classifier`

必要 sections：`Background / Goal / Scope / Public Interface / Acceptance Criteria / Constraints`

模型選擇建議：
- 標準計算 → `sonnet`
- 簡單驗證（Luhn/regex/format）→ `haiku`
- 涉及多階段狀態或稅務規則 → `opus`

### `kind: router`

用在 **FastAPI 路由 + service 層 + DB 交易** 的整合式功能。範例：
`orders_router`, `stock_intake_router`, `clock_router`, `cost_events_router`

必要 sections：`Background / Routes / Pydantic Schemas / Database writes /
Acceptance Criteria / Error responses / Out of scope`

模型選擇建議：
- 交易邏輯簡單 → `sonnet`
- 涉及跨表更新、事務邊界、複雜錯誤處理 → `opus`
- 純 CRUD → `haiku`（罕見）

---

## 常用指令速查

```bash
# 檢查所有 spec 是否符合契約
make spec-check

# 檢查單一 spec
python3.12 scripts/validate_spec.py specs/<id>.md

# 自動修正 frontmatter 的 ac_count 欄位
python3.12 scripts/validate_spec.py --fix-counts specs/<id>.md

# 對單一 spec 跑三模型 bakeoff
make bakeoff SPEC=specs/<id>.md

# 只跑特定模型組合
make bakeoff SPEC=specs/<id>.md MODELS=sonnet,haiku

# 用 spec 檔跑 swarm（推薦）
make swarm SPEC=specs/<id>.md

# inline 快跑（prototype 專用）
make swarm REQ="Build me a X that Y"

# 完整品質關卡（含 spec-check）
make full-check
```

---

## 常見錯誤（與如何避免）

| 錯誤 | 為什麼發生 | 避免方法 |
|---|---|---|
| 手寫 spec 沒 frontmatter → validator fail | 忘記走 `/spec` | 硬規 #1 |
| spec 只有 3 個 AC → validator fail | 沒對照 profit_calc.md 抄 | `/spec` 的 agent 會強制 ≥10 |
| ACs 用「大約」「應該」不給具體數字 → Coder 亂猜 | 寫太趕 | 步 2 人審必看：每條 AC 有沒有具體輸入輸出數字 |
| 用 Opus 跑本可 Sonnet 過的 spec → 燒錢 | 沒跑 bakeoff 就 /swarm | 硬規 #3 |
| Coder 越權寫 DB / HTTP → 破壞 pure-function 邊界 | Out of scope 沒列清楚 | 步 2 人審必看：out-of-scope 至少 3 條 |
| pytest 綠但 domain 錯（例：金額算對但把 comp 當成 amount）| AC 沒覆蓋語意邊界 | 步 2 人審必問「什麼情境沒測到」 |

---

## 未來延伸

當這套走順（大概 5-10 個新 spec 後）可以考慮的升級：

1. **`kind: service`** — 介於 pure-function 與 router 之間的 service layer 模組
2. **`spec` 之間的相依性宣告** — `depends_on: [profit_calc, discount_resolver]`
3. **多 tenant 感知的 bakeoff** — 對特定 tenant 資料跑，看模型能否正確處理
4. **Cost budget 全域上限** — 月度 $50 上限，達到就自動阻擋新 `/swarm`
5. **Spec 版本化** — `version: 2` 表示改過語意，需要重跑 bakeoff

---

## 相關文件

- `CLAUDE.md` — 專案總記憶（包含這份 playbook 的簡化總結）
- `docs/02_devswarm_architecture.md` — LangGraph 拓撲與 agent 分工
- `docs/07_devswarm_runbook.md` — `make demo` 故障排除
- `docs/10_claude_code_workflow.md` — Claude Code 通用能力對照
- `scripts/validate_spec.py` — 契約實作
- `scripts/bakeoff.py` — 模型並排 harness
- `.claude/commands/spec.md` / `swarm.md` / `bakeoff.md` — 三個關鍵 slash command

---

## 一句話收尾

紀律不是「每次都想起來要走全套」— 那不可能。紀律是「機制擋住不合規、
習慣讓合規的路成為最省力的路」。這份文件的目的：讓合規的路變成
`/spec` → `/bakeoff` → `/swarm`，任何其他路都會撞到 gate。

— end of playbook —
