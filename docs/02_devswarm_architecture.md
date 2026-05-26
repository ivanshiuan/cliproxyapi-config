# 02 — DevSwarm Architecture（蜂群骨架手冊）

**Status:** Canonical architecture for DevSwarm v1 (this repo's first deliverable).
**Scope:** The 4-agent LangGraph swarm that turns commander requests into working Python modules with passing tests.
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

對立面（為什麼不做 ReAct loop 單 Agent）：

- ReAct 在「需要長期記住多個產出（PRD、constraints、code、tests）」時，context 會被工具呼叫紀錄稀釋，主訊息易被截斷
- 單 Agent 在不同任務階段使用同一模型 → 不能成本最佳化

---

## 2. The four roles（四個角色）

| Role | Model | Reads from state | Writes to state | Rationale |
|---|---|---|---|---|
| **PM Agent** | `claude-opus-4-7` | `task_brief` | `prd`, `messages[+]` | 把指揮官的非結構化需求 → 結構化 PRD（problem / scope / acceptance criteria / non-goals）。需要長思考、抽象建模能力 → Opus |
| **Architect Agent** | `claude-opus-4-7` | `task_brief`, `prd` | `constraints`, `messages[+]` | 在 PRD 上疊資安、效能、合規約束（個資法、SQL injection 防禦、輸入驗證、log 不洩個資）。需要深度技術判斷 → Opus |
| **Coder Agent** | `claude-sonnet-4-6` | `task_brief`, `prd`, `constraints`, `qa_report`(若有) | `files_written`, `messages[+]`, `heal_iter+=1`（heal 時） | 寫程式 + 寫測試。Sonnet 4.6 是程式碼 first-try 通過率與成本的甜蜜點 |
| **QA Agent** | `claude-haiku-4-5-20251001` | `files_written`, pytest output | `tests_passed`, `qa_report`, `messages[+]` | 跑 pytest，解析失敗、產出機械式報告。不需要創意，要的是穩定 + 便宜 → Haiku |

> 模型 ID 與 [`../.env.example`](../.env.example) 中 `DEVSWARM_MODEL_*` 對應；可由環境變數覆寫。

---

## 3. Graph topology（圖結構）

```
                       ┌─────────────────────────────┐
                       │       SwarmState (dict)     │
                       │  task_brief / prd /         │
                       │  constraints / files /      │
                       │  qa_report / heal_iter / …  │
                       └──────────────┬──────────────┘
                                      │
              ┌───────────────────────┴───────────────────────┐
              │                                               │
              ▼                                               │
   ┌────────────────────┐                                     │
   │     START          │                                     │
   └──────────┬─────────┘                                     │
              │                                               │
              ▼                                               │
   ┌────────────────────┐   produces PRD                      │
   │   PM Agent (Opus)  │ ──────────────────────────┐         │
   └──────────┬─────────┘                           │         │
              │                                     │         │
              ▼                                     │         │
   ┌────────────────────┐   adds security /         │         │
   │ Architect (Opus)   │   architecture constraints│         │
   └──────────┬─────────┘                           │         │
              │                                     │         │
              ▼                                     │         │
   ┌────────────────────┐                           │         │
   │  Coder (Sonnet)  ◄─┼─────────────┐             │         │
   │  write_file /      │             │             │         │
   │  read_file /       │             │             │         │
   │  list_files        │             │             │         │
   └──────────┬─────────┘             │             │         │
              │                       │             │         │
              ▼                       │ heal        │         │
   ┌────────────────────┐             │ (qa_report) │         │
   │   QA (Haiku)       │─────────────┤             │         │
   │   runs pytest      │             │             │         │
   └──────────┬─────────┘             │             │         │
              │ branch                │             │         │
              ├──── tests_passed ─────┼──► END      │         │
              │     OR                │             │         │
              │     heal_iter ≥ max   │             │         │
              │                       │             │         │
              └── !passed & heal<max ─┘             │         │
                                                    │         │
                       (state mutation flows)       │         │
                                                    ▼         ▼
```

### Conditional edge：QA 之後的分支邏輯

```python
def route_after_qa(state: SwarmState) -> Literal["coder", "__end__"]:
    if state["tests_passed"]:
        return "__end__"
    if state["heal_iter"] >= state["max_heal_iters"]:
        # exhausted; bail with last qa_report intact
        return "__end__"
    return "coder"
```

不可達分支必須 log warning，避免無聲掉資料。

---

## 4. State schema（SwarmState TypedDict）

DevSwarm 的所有節點共享同一個 `SwarmState`（定義在 `../devswarm/state.py`）。下表列出每個欄位的契約。**新增欄位必須先改本表 + state.py，再改 agent。**

| Field | Type | Written by | Read by | 用途 |
|---|---|---|---|---|
| `task_id` | `str` | entry point (CLI) | all | 用於 `workspace/<task_id>/` 隔離與 log 標籤 |
| `task_brief` | `str` | entry point | PM, Architect, Coder | 指揮官原始需求（自然語言） |
| `prd` | `str \| None` | PM | Architect, Coder | 結構化需求文件（problem / scope / acceptance / non-goals） |
| `constraints` | `list[str]` | Architect | Coder | 安全 / 架構約束逐條列出 |
| `files_written` | `dict[str, str]` | Coder | QA | `{relative_path: content_hash}` 便於 audit；實體檔案落地在 `workspace/<task_id>/` |
| `tests_passed` | `bool` | QA | router | pytest exit code 是否 0 |
| `qa_report` | `str \| None` | QA | Coder (heal) | 失敗摘要 + 建議；通過時為 `None` |
| `heal_iter` | `int` | Coder（每次 heal 自增） | router | 自我修復回合計數，從 0 開始 |
| `max_heal_iters` | `int` | entry point | router | 上限，預設 5（`DEVSWARM_MAX_HEAL_ITERS`） |
| `messages` | `list[AnyMessage]` | all | all (audit) | LangGraph 對話歷史，含每個 agent 的 system / assistant / tool turn |
| `usage` | `dict[str, dict]` | all (after each call) | observer | `{agent_name: {input_tokens, output_tokens, cache_creation, cache_read}}` |
| `started_at` | `datetime` | entry point | observer | UTC ISO timestamp |
| `finished_at` | `datetime \| None` | router (terminal) | observer | UTC ISO timestamp |
| `final_status` | `Literal["success","failed","exhausted"] \| None` | router (terminal) | CLI return code | success: tests pass / failed: error / exhausted: heal 用盡 |

### Invariants（不可違反）

1. `heal_iter` 單調遞增，不允許回退
2. `tests_passed=True` 時 `qa_report` 必為 `None`，反之亦然
3. `prd` 一旦由 PM 寫入，後續節點不得修改（only-append 心智模型）
4. `files_written` 是 hash map；實體檔由 Coder 透過工具寫入 `workspace/<task_id>/`，狀態只記錄指紋

---

## 5. Self-heal loop（自我修復循環）

### 5.1 機制

```
Coder writes files ─► QA runs pytest ─► QA produces report
                                              │
                                              ▼
                            tests_passed?  ─► END (success)
                                              │
                                              ▼
                            heal_iter < max? ─► loop back to Coder with qa_report
                                              │
                                              ▼
                                              END (exhausted)
```

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

---

## 6. Prompt caching strategy（提示快取策略）

Anthropic prompt caching: 寫快取要錢（1.25× 一般輸入），但讀快取只要 0.1×（**便宜 90%**）。DevSwarm 是「同樣 system prompt 重複跑」的典型場景，必須用滿。

### 6.1 哪些 prompt 段落要快取

| Agent | Cached（`cache_control: ephemeral`） | Not cached |
|---|---|---|
| PM | system prompt（角色、PRD 模板、輸出格式） | user message（task_brief） |
| Architect | system prompt（資安規則庫、constraint 模板）+ PRD（每任務內 N 回不變） | user message |
| Coder | system prompt（工具說明、coding standards）+ PRD + constraints | qa_report（每 heal 變動） |
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
    --workspace-root ./workspace
```

退出碼：

| Code | 對應 `final_status` |
|---|---|
| 0 | `success` |
| 1 | `failed`（內部錯誤、API 失敗） |
| 2 | `exhausted`（heal 用盡仍 tests fail） |

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
