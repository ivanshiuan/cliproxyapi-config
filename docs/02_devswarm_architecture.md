# 02 — DevSwarm Architecture（蜂群骨架手冊）

**Status:** Canonical architecture for DevSwarm v1 (this repo's first deliverable).
**Scope:** The **5-agent** LangGraph swarm that turns commander requests into working Python modules with passing tests. v1.1 加入第 5 個角色 **Reviewer（對抗式審查）**——靈感來自 [4x framework](https://github.com/ggwhite/4x) 的 Designer→Coder→**Reviewer**→Tester 角色隔離；把「測試綠 ≠ 邏輯對」這個盲點補上（見 §1 理由 5、§2 角色表）。
**Out of scope:** RestSwarm application stack ([`./01_tech_stack_recommendation.md`](./01_tech_stack_recommendation.md)), restaurant schema ([`./04_data_schema.md`](./04_data_schema.md)), product roadmap ([`./03_roadmap.md`](./03_roadmap.md)).
**SSOT alignment:** [`./00_vision.md`](./00_vision.md) §五 Layer 1.

> 此文件是 DevSwarm 的「機殼設計圖」。讀完，任何後續工程師（或 AI）必能擴充、debug、替換零件。

---

## 1. Why a swarm, not a single agent（為什麼是蜂群，不是單一 Agent）

單一巨型 prompt 給一個 Claude，理論上也能寫程式碼。我們不那麼做，理由有四：

| # | 理由 | 說明 |
|---|---|---|
| 1 | **角色專業化** | PM 寫 PRD 的 prompt、Architect 注入資安、Coder 寫 code、QA 跑測試——四種 system prompt 互不污染。單一 agent 必須在 prompt 中切換人格，正確率下降 |
| 2 | **模型成本配對** | Opus 4.7 適合長思考（PM、Architect），Sonnet 4.6 是程式碼最佳性價比，Haiku 4.5 跑機械式測試報告。每環節用對模型，token 成本可降 40-60% |
| 3 | **可組合性** | LangGraph 把每個節點當 pure function（state in → state out）；要加 DevOps Agent / Security Agent，只是加一個 node + 一條 edge，不必重寫主邏輯 |
| 4 | **可觀測 / 可中斷** | 每個 agent 都把訊息追加到 `messages` 陣列；中途任何環節都可 inspect、replay、人工接管 |
| 5 | **對抗式審查 / 防自審** | Coder 不能自己 approve。獨立 **Reviewer** 角色**看不到 Coder 的推理過程**（不讀 Coder 的 `messages`），只審成品 diff + PRD + constraints。pytest 綠燈只證明「測得到的有過」，證明不了金額算對、ledger 有記、軟刪除沒漏——這類錯誤要靠對抗式人格盯。此為 4x framework 的核心教訓，內化進蜂群而非外掛工具 |

對立面（為什麼不做 ReAct loop 單 Agent）：

- ReAct 在「需要長期記住多個產出（PRD、constraints、code、tests）」時，context 會被工具呼叫紀錄稀釋，主訊息易被截斷
- 單 Agent 在不同任務階段使用同一模型 → 不能成本最佳化

---

## 2. The four roles（四個角色）

| Role | Model | Reads from state | Writes to state | Rationale |
|---|---|---|---|---|
| **PM Agent** | `claude-opus-4-7` | `task_brief` | `prd`, `messages[+]` | 把指揮官的非結構化需求 → 結構化 PRD（problem / scope / acceptance criteria / non-goals）。需要長思考、抽象建模能力 → Opus |
| **Architect Agent** | `claude-opus-4-7` | `task_brief`, `prd` | `constraints`, `messages[+]` | 在 PRD 上疊資安、效能、合規約束（個資法、SQL injection 防禦、輸入驗證、log 不洩個資）。需要深度技術判斷 → Opus |
| **Coder Agent** | `claude-sonnet-4-6` | `task_brief`, `prd`, `constraints`, `review_findings`(若有), `qa_report`(若有) | `files_written`, `messages[+]`, `heal_iter+=1`（heal 時） | 寫程式 + 寫測試。Sonnet 4.6 是程式碼 first-try 通過率與成本的甜蜜點 |
| **Reviewer Agent** | `claude-opus-4-7` | `task_brief`, `prd`, `constraints`, `files_written`（**只看成品 diff，不讀 Coder 的 `messages`**） | `review_passed`, `review_findings`, `review_iter+=1`, `messages[+]` | 對抗式審查：假設 code 有錯，逐條對 PRD acceptance criteria + 專案不變法則（`Decimal`、append-only ledger、軟刪除、稽核走 service）查。需要深度判斷且要敢擋 → Opus。可由 `DEVSWARM_MODEL_REVIEWER` 降級成 Sonnet 換成本 |
| **QA Agent** | `claude-haiku-4-5-20251001` | `files_written`, pytest output | `tests_passed`, `qa_report`, `messages[+]` | 跑 pytest，解析失敗、產出機械式報告。不需要創意，要的是穩定 + 便宜 → Haiku |

> 模型 ID 與 [`../.env.example`](../.env.example) 中 `DEVSWARM_MODEL_*` 對應；可由環境變數覆寫。

### 2.1 對抗式隔離（為什麼 Reviewer 看不到 Coder 推理）

單一 agent「自己寫、自己 review、自己說 LGTM」是假審查——它會替自己的設計合理化。Reviewer 的價值來自**資訊隔離**：

- Reviewer **只收成品**（`files_written` 的實體內容）+ 任務契約（`prd`、`constraints`），**不收** Coder 的 `messages`（思路、自辯、heal 對話）。
- Reviewer 用的 acceptance criteria 來自 **PM 寫的 PRD**，不是 Coder 自己宣稱的「我覆蓋了哪些 case」。
- Reviewer 與 Coder **不同模型實例、不同 system prompt**，人格不互相污染（同 4x 的「Coder never sees the Reviewer's reasoning」對偶）。

這條隔離是 architecture invariant，不可為了省 token 把 Coder 的 messages 餵給 Reviewer（見 §4 invariant 6）。

---

## 3. Graph topology（圖結構）

```
  START
    │
    ▼
  PM (Opus) ──► Architect (Opus) ──► Coder (Sonnet) ──► Reviewer (Opus) ──► QA (Haiku) ──► END
   produces        adds sec/arch        write/read/         adversarial         runs           (success)
   PRD             constraints          list_files          code review         pytest
                                            ▲                    │                  │
                                            │                    │ !review_passed   │ !tests_passed
                                            │   review_findings  │ & review_iter<max│ & heal_iter<max
                                            │◄───────────────────┘                  │
                                            │                       qa_report        │
                                            │◄───────────────────────────────────────┘
                                            └─ 兩條回邊都回到 Coder（先過審查再跑測試）

  Reviewer 是新 gate：Coder 每次產出都要先被 Reviewer 審過才進 QA。
  review 用盡 → exhausted；review 過了但 QA fail → 走 qa heal；兩者都過 → success。
```

> **兩個獨立回饋迴路**：`review_findings`（靜態審查抓邏輯/合規錯）與 `qa_report`（動態測試抓失敗）。每個 Coder iteration 之後**先 Reviewer、後 QA**——重審修正版的代價就是 4x 文件所稱「3–10× 單 agent token」，這是用 token 換正確性，對金額/ledger 任務值得；可用 §4 的 `review_iter`/`heal_iter` 上限與 `cost_limit_usd` 封頂。

### Conditional edge：Reviewer 之後的分支邏輯

```python
def route_after_review(state: SwarmState) -> Literal["coder", "qa", "__end__"]:
    if state["review_passed"]:
        return "qa"                      # 審查過 → 跑測試
    if state["review_iter"] >= state["max_review_iters"]:
        return "__end__"                 # 審查回合用盡 → exhausted（不浪費 QA）
    return "coder"                       # 帶 review_findings 回 Coder 修
```

### Conditional edge：QA 之後的分支邏輯

```python
def route_after_qa(state: SwarmState) -> Literal["coder", "__end__"]:
    if state["tests_passed"]:
        return "__end__"
    if state["heal_iter"] >= state["max_heal_iters"]:
        # exhausted; bail with last qa_report intact
        return "__end__"
    return "coder"                       # 回 Coder → 修正版會再經 Reviewer 才回 QA
```

不可達分支必須 log warning，避免無聲掉資料。兩個 router 共用 `cost_limit_usd` 軟上限：任一回邊前若預估成本超標即提前 `__end__`。

---

## 4. State schema（SwarmState TypedDict）

DevSwarm 的所有節點共享同一個 `SwarmState`（定義在 `../devswarm/state.py`）。下表列出每個欄位的契約。**新增欄位必須先改本表 + state.py，再改 agent。**

| Field | Type | Written by | Read by | 用途 |
|---|---|---|---|---|
| `task_id` | `str` | entry point (CLI) | all | 用於 `workspace/<task_id>/` 隔離與 log 標籤 |
| `task_brief` | `str` | entry point | PM, Architect, Coder | 指揮官原始需求（自然語言） |
| `prd` | `str \| None` | PM | Architect, Coder | 結構化需求文件（problem / scope / acceptance / non-goals） |
| `constraints` | `list[str]` | Architect | Coder | 安全 / 架構約束逐條列出 |
| `files_written` | `dict[str, str]` | Coder | **Reviewer**, QA | `{relative_path: content_hash}` 便於 audit；實體檔案落地在 `workspace/<task_id>/` |
| `review_passed` | `bool` | Reviewer | router | 對抗式審查是否放行；`False` → 回 Coder 或 exhausted |
| `review_findings` | `str \| None` | Reviewer | Coder (heal) | 阻擋性發現（逐條：嚴重度 / 位置 / 為何錯 / 對應哪條 AC 或不變法則）；放行時為 `None` |
| `review_iter` | `int` | Reviewer（每次審查自增） | router | 審查回合計數，從 0 開始；**與 `heal_iter` 分開計** |
| `max_review_iters` | `int` | entry point | router | 上限，預設 3（`DEVSWARM_MAX_REVIEW_ITERS`） |
| `tests_passed` | `bool` | QA | router | pytest exit code 是否 0 |
| `qa_report` | `str \| None` | QA | Coder (heal) | 失敗摘要 + 建議；通過時為 `None` |
| `heal_iter` | `int` | Coder（每次 heal 自增） | router | 自我修復回合計數，從 0 開始 |
| `max_heal_iters` | `int` | entry point | router | 上限，預設 5（`DEVSWARM_MAX_HEAL_ITERS`） |
| `messages` | `list[AnyMessage]` | all | all (audit) | LangGraph 對話歷史，含每個 agent 的 system / assistant / tool turn |
| `usage` | `dict[str, dict]` | all (after each call) | observer | `{agent_name: {input_tokens, output_tokens, cache_creation, cache_read}}` |
| `started_at` | `datetime` | entry point | observer | UTC ISO timestamp |
| `finished_at` | `datetime \| None` | router (terminal) | observer | UTC ISO timestamp |
| `final_status` | `Literal["success","failed","exhausted"] \| None` | router (terminal) | CLI return code | success: 審查放行且 tests pass / failed: error / exhausted: review **或** heal 回合用盡 |

### Invariants（不可違反）

1. `heal_iter`、`review_iter` 皆單調遞增，不允許回退
2. `tests_passed=True` 時 `qa_report` 必為 `None`，反之亦然；`review_passed=True` 時 `review_findings` 必為 `None`，反之亦然
3. `prd` 一旦由 PM 寫入，後續節點不得修改（only-append 心智模型）
4. `files_written` 是 hash map；實體檔由 Coder 透過工具寫入 `workspace/<task_id>/`，狀態只記錄指紋
5. **QA 只在 `review_passed=True` 後執行**——未過審查的 code 不浪費 pytest 資源；Coder 修正版必先重經 Reviewer
6. **Reviewer 絕不讀 Coder 的 `messages`**（對抗式隔離，見 §2.1）；只能讀 `files_written`、`prd`、`constraints`。違反即退化成「自審」，失去整個角色的意義

---

## 5. Self-heal loop（自我修復循環）

### 5.1 機制

```
Coder writes files ─► Reviewer inspects diff ─► review_passed?
                                                     │ no
                                                     ▼
                                  review_iter < max? ─► loop back to Coder with review_findings
                                                     │ no → END (exhausted)
                                                     │ yes (passed)
                                                     ▼
                              QA runs pytest ─► QA produces report
                                                     │
                                                     ▼
                                   tests_passed?  ─► END (success)
                                                     │ no
                                                     ▼
                                   heal_iter < max? ─► loop back to Coder with qa_report
                                                     │
                                                     ▼
                                                     END (exhausted)
```

> 注意順序：**審查在測試之前**。Reviewer 抓的是「測試抓不到」的類別（金額符號錯、漏記 ledger、軟刪除沒套、稽核沒走 service、邊界未處理），這些往往連測試案例本身都漏寫；先擋下來再跑 pytest 才有意義。

### 5.2 max_heal_iters = 5（為什麼）

| 回合 | 成功率（經驗值） | 累計成功率 |
|---|---|---|
| 1 (first try) | ~55% | 55% |
| 2 | ~25% | 80% |
| 3 | ~10% | 90% |
| 4 | ~5% | 95% |
| 5 | ~3% | 98% |
| 6+ | <1% per round | 邊際效益 → 直接 exhausted 比較划算 |

第 6 回合通常已是「需求本身不明確」或「測試案例有 bug」，這種情況該由人介入而非繼續燒 token。預設值 5 可由 `DEVSWARM_MAX_HEAL_ITERS` 覆寫。

### 5.3 QA report 格式（Coder 消化用）

QA Agent 必須產出**結構化**的 markdown 報告：

```markdown
## QA Report — task <task_id> — iter <n>

### Pytest exit code
1 (non-zero)

### Failed tests (3)
1. `tests/test_profit.py::test_real_net_profit_includes_complimentary`
   - Assertion: expected 12500, got 15000
   - Likely cause: complimentary line items not deducted from gross
2. ...

### Suggested fixes
- In `profit.py:calculate_net()`, subtract `sum(complimentary_items)` before tax
- Add edge case: empty order list should return 0 not raise

### Stack trace excerpt (truncated)
```
Coder 在 heal 回合會把上一輪的 `qa_report` 注入 system prompt（cached 部分之外的 ephemeral 段落），明確要求只改修 listed 失敗。

### 5.4 Heal 之間的狀態保留

- `prd`、`constraints` **絕不重新生成**（節省 token 與避免漂移）
- `files_written` 在 Coder 重跑時可被覆寫（同 path 直接寫新內容）
- `messages` 持續 append，不清空（便於事後 audit）
- Coder 回邊時，**只**注入當下那條回饋（review heal 注 `review_findings`、qa heal 注 `qa_report`），不混餵，避免上下文稀釋

### 5.5 Review gate（審查閘門）

#### 為什麼 `max_review_iters = 3`（比 heal 少）

審查回合是「Reviewer 嫌 → Coder 改 → Reviewer 再嫌」。實務上前 2 回合能收斂掉九成阻擋性發現；第 3 回合還擋不下，多半是 **PRD 本身對需求/合規的描述不夠明確**，該由人介入補 spec，而不是讓 Opus 互相拉鋸燒 token。預設 3，可由 `DEVSWARM_MAX_REVIEW_ITERS` 覆寫。

#### Review report 格式（Coder 消化用）

Reviewer 必須輸出**結構化** markdown，每條發現綁一個嚴重度與一條依據（AC 編號或專案不變法則）：

```markdown
## Review Report — task <task_id> — iter <n>

### Verdict
BLOCK   (review_passed = false)

### Blocking findings (2)
1. [CRITICAL] `profit.py:calculate_net()` — 用 float 累加金額
   - 違反不變法則「所有金錢用 Decimal」
   - 後果：四捨五入漂移會進損益表，無法稽核
2. [HIGH] `stock.py:consume()` — 直接 UPDATE stock_movements
   - 違反「ledger 表 append-only」；DB RULE 會擋，但 code 不該嘗試
   - 應改為 append 一筆 reversal

### Non-blocking notes (advisory, 不擋)
- `discount.py` 的型別註記可更精確（不影響正確性）

### What is NOT in scope to fix
- 既有檔案的風格；只審本任務 diff
```

- `BLOCK` → router 走 `route_after_review` 回 Coder（注 `review_findings`）。
- `PASS`（無 blocking finding）→ `review_passed=True`、`review_findings=None` → 進 QA。
- Reviewer **只標問題、不改 code**（改 code 是 Coder 的事，維持角色純粹 + 對抗性）。

---

## 6. Prompt caching strategy（提示快取策略）

Anthropic prompt caching: 寫快取要錢（1.25× 一般輸入），但讀快取只要 0.1×（**便宜 90%**）。DevSwarm 是「同樣 system prompt 重複跑」的典型場景，必須用滿。

### 6.1 哪些 prompt 段落要快取

| Agent | Cached（`cache_control: ephemeral`） | Not cached |
|---|---|---|
| PM | system prompt（角色、PRD 模板、輸出格式） | user message（task_brief） |
| Architect | system prompt（資安規則庫、constraint 模板）+ PRD（每任務內 N 回不變） | user message |
| Coder | system prompt（工具說明、coding standards）+ PRD + constraints | review_findings / qa_report（每 heal 變動） |
| Reviewer | system prompt（審查 rubric + 專案不變法則清單）+ PRD + constraints | files content（每次 diff 變動） |
| QA | system prompt（pytest 報告模板） | files content + pytest output |

### 6.2 預期省幅

以一個典型 4 回合 heal 的任務估算：

| 來源 | No cache (tokens) | With cache (tokens) | 節省 |
|---|---:|---:|---:|
| Coder system + PRD + constraints × 4 回 | ~16,000 | 4,000 寫 + 12,000 讀 (×0.1) | ~70% |
| QA system × 4 回 | ~4,000 | 1,000 寫 + 3,000 讀 (×0.1) | ~67% |
| **整體輸入成本** | 1.00× | **~0.35×** | **~65%** |

### 6.3 觀測

每次 API 呼叫回應的 `usage.cache_creation_input_tokens` 與 `usage.cache_read_input_tokens` 都寫入 `state["usage"]`，供事後成本帳分析。

---

## 7. Sandbox model（沙盒模型）

### 7.1 隔離邊界

每個任務：

```
workspace/
└── <task_id>/                  ← Coder 工具只能寫這個目錄底下
    ├── src/
    │   └── *.py
    ├── tests/
    │   └── test_*.py
    └── pyproject.toml (optional)
```

- Coder 的 `write_file` 強制 path resolve 到 `workspace/<task_id>/` 內，超出即 raise `SandboxEscapeError`
- `read_file`、`list_files` 同樣限定根目錄

### 7.2 pytest 執行

```python
subprocess.run(
    ["pytest", "-q", "--tb=short"],
    cwd=f"workspace/{task_id}",
    timeout=int(os.getenv("DEVSWARM_SANDBOX_TIMEOUT", "60")),  # 60s default
    preexec_fn=apply_rlimits,                                  # CPU/MEM/FILE caps
    capture_output=True,
    text=True,
)
```

`apply_rlimits` 透過 `resource.setrlimit` 套用：

| Limit | Value |
|---|---|
| `RLIMIT_CPU` | 60s soft / 90s hard |
| `RLIMIT_AS`（virtual mem） | 1 GiB |
| `RLIMIT_NOFILE` | 256 |
| `RLIMIT_FSIZE` | 50 MiB / 檔 |

### 7.3 明確限制（v1 不做的）

> **WARNING**：v1 **沒有** container-level isolation。Coder 寫的 Python 在 swarm process 的 user 權限下執行 pytest。**惡意或脫軌的 LLM 產出可能讀寫該 user 能存取的任何檔案。**

緩解：

1. 在 VM / container 中跑 swarm 本身（推薦 rootless Docker / firecracker / nsjail）
2. 該 user 不應持有任何 production credential / cloud key
3. CI 環境跑 swarm 時，使用 ephemeral runner

v2 計畫：把 pytest subprocess 換成 Docker SDK 啟動的 disposable container；或上 `gVisor`。見 §10 deferred。

---

## 8. Tool surface（Coder 可呼叫的工具）

透過 Anthropic Tool Use API 暴露給 Coder Agent：

| Tool | Signature | 描述 |
|---|---|---|
| `write_file` | `(path: str, content: str) -> {ok: bool, bytes_written: int}` | 在 `workspace/<task_id>/` 下寫檔；自動建立父目錄 |
| `read_file` | `(path: str) -> {content: str}` | 讀已寫入的檔；不存在則回 error |
| `list_files` | `(subdir: str \| None = None) -> {paths: list[str]}` | 列出沙盒內檔案 |

**未來才加**（v2）：

- `run_shell`（限定白名單命令，目前不開放）
- `search_web`（給 PM Agent 查台灣餐飲業資料，須有 quota）
- `query_db`（給 QA 跑 schema 驗證）

PM / Architect / QA 在 v1 **無工具**，純文字 in/out。

---

## 9. Observability（觀測）

### 9.1 即時

- `state["messages"]` 是 LangGraph 標準訊息陣列，可直接接 LangSmith / LangFuse；本地用 structlog JSON dump
- 每個 node 進出寫一筆 `node_start` / `node_end` 事件，含 `task_id`、`agent`、`heal_iter`、`elapsed_ms`

### 9.2 成本追蹤

每次 API call 結束後將 `response.usage` 解構並 accumulate：

```python
state["usage"][agent_name] = {
    "input_tokens":      ...,
    "output_tokens":     ...,
    "cache_creation_input_tokens": ...,
    "cache_read_input_tokens":     ...,
    "calls":             ...,
}
```

run 結束後可直接套官方價目計算單任務成本。

### 9.3 Audit log

- 完整 `messages` JSON 落到 `workspace/<task_id>/_audit.jsonl`
- `final_state.json` 儲存最終 state（檔列表、tests_passed、usage）
- 失敗 case 額外保留 pytest 原始輸出於 `workspace/<task_id>/_pytest.log`

---

## 10. What's deferred（明確延後）

| 項目 | 延後到 | 理由 |
|---|---|---|
| DevOps Agent（自動 Dockerize / Helm chart） | DevSwarm v2 | Phase 1 還沒上 K8s |
| Checkpointer / resume from mid-run | v2 | LangGraph SQLite checkpoint 易加，但 v1 任務粒度小，不需要 |
| 平行任務（多 task 同時跑） | v2 | 需要 work queue + resource scheduler |
| RAG / 長期記憶 | v3 | 等 RestSwarm schema 穩定後再餵 |
| Web UI | v2 | v1 CLI 即可：`python -m devswarm --task-file specs/foo.md` |
| 多語言 code-gen | v3 | v1 **only Python**；其他語言需新工具集 + 新測試 runner |
| Container-level sandbox | v2 | 用 Docker SDK 或 firecracker 替換 subprocess（見 §7.3） |
| 自動 PR / GitHub 整合 | v2 | 蜂群完工後自動開 PR、跑 CI |
| Multi-tenant DevSwarm-as-a-Service | v4+ | 商業化遠期 |

---

## 11. Extension points（如何擴充）

### 11.1 新增 Agent 角色（例：Security Reviewer）

> v1.1 的 **Reviewer** 就是照這套 SOP 加進來的——把它當作已完成的參考實作（§2 角色表、§3 拓撲 router、§4 state 欄位 + invariant 5/6、§5.5 報告格式、§6.1 cache）。要再加角色照下列步驟。

1. 在 `state.py` 加欄位（例：`security_findings: list[str]`）
2. 新增 `devswarm/agents/security.py`，函式簽名 `def security_node(state: SwarmState) -> dict: ...` 回傳 partial state update
3. 在 `devswarm/graph.py` 加 node + edge：
   ```python
   builder.add_node("security", security_node)
   builder.add_edge("architect", "security")
   builder.add_edge("security", "coder")
   ```
4. 為新 agent 加 system prompt + cache 設定
5. 更新本文 §2 角色表 + §4 state schema

### 11.2 新增工具給 Coder

1. 在 `devswarm/tools/` 新增 `my_tool.py`，定義 `TOOL_SCHEMA` (Anthropic tool spec) + `def execute(args) -> dict`
2. 在 `devswarm/agents/coder.py` 的 tool registry 註冊
3. 加單元測試於 `tests/tools/test_my_tool.py`
4. 更新本文 §8 表

### 11.3 換掉 LLM provider

抽象層 `devswarm/llm/client.py` 包一層 `LLMClient` protocol：

```python
class LLMClient(Protocol):
    def chat(self, *, system: str, messages: list, tools: list | None,
             cache_breakpoints: list[int] | None) -> Response: ...
```

目前實作：`AnthropicClient`。要接 OpenAI / Bedrock / 本地 vLLM，新增對應實作 + factory；外層 graph 不動。

> ⚠️ Prompt caching API 各家不一致；換 provider 時需重做 cache 策略（見 §6）。

---

## 12. Threat model summary（威脅模型摘要）

| Threat | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 惡意 LLM 產出讀寫沙盒外檔案 | Medium | High | §7.1 path enforcement；strongly recommend VM/container around swarm |
| pytest 跑出無限迴圈 / fork bomb | Medium | Medium | rlimits（CPU/MEM/NOFILE）+ subprocess timeout |
| Coder 寫出 `subprocess.run(...)` 在測試中執行任意命令 | Medium | High | rlimits 限制 + 不在沙盒 user 持有任何 prod credential |
| Anthropic API key 洩漏 | Low | High | `.env` not committed；CI 用 secret store；建議 short-lived key |
| Prompt injection via `task_brief` | Low | Low–Medium | Architect Agent 會把 task_brief 視為 untrusted data；不執行其中指令 |
| 蜂群跑爆 token quota / 預算 | Medium | Medium | §9.2 cost tracking；CLI 加 `--max-cost-usd` 軟上限 |

**強烈建議**：

1. 在 throwaway VM / unprivileged container 跑 swarm
2. 不要把 swarm process 接到 production DB / cloud admin credential
3. 跑長任務時開啟 LangSmith / LangFuse 觀測

---

## 13. CLI surface（入口）

```bash
python -m devswarm \
    --task-file specs/profit_calc.md \
    --task-id profit_calc_20260526 \
    --max-heal-iters 5 \
    --max-review-iters 3 \
    --workspace-root ./workspace
```

退出碼：

| Code | 對應 `final_status` |
|---|---|
| 0 | `success`（審查放行且 tests pass） |
| 1 | `failed`（內部錯誤、API 失敗） |
| 2 | `exhausted`（review **或** heal 回合用盡） |

> 退出碼 2 時看 `final_state.json` 的 `review_findings` vs `qa_report` 哪個非空，即知卡在審查還是測試。

---

## 14. 與其他文件的關係

| 文件 | 關係 |
|---|---|
| [`./00_vision.md`](./00_vision.md) | 上游 SSOT |
| [`./01_tech_stack_recommendation.md`](./01_tech_stack_recommendation.md) | 平行；本文管 DevSwarm 工廠，它管 RestSwarm 產出物 |
| [`./03_roadmap.md`](./03_roadmap.md) | DevSwarm = Phase 0；本文是 Phase 0 的內部設計 |
| [`./04_data_schema.md`](./04_data_schema.md) | 不直接相關；DevSwarm 不需要 RestSwarm 的 DB |
| [`../.env.example`](../.env.example) | 環境變數規範 |
| `../devswarm/state.py` | 本文 §4 的程式實作 |
| `../devswarm/graph.py` | 本文 §3 的程式實作 |
