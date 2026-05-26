# 07 — DevSwarm 操作手冊

> 指揮官早上第一次跑 `make demo` 時的隨身指南。
> 包含預期輸出、典型錯誤、成本上限、自救步驟。

---

## 0. 一次性設定

```bash
# 1. 環境變數
cp .env.example .env
# 編輯 .env，填入：
#   ANTHROPIC_API_KEY=sk-ant-api03-...
# 從 https://console.anthropic.com/settings/keys 拿

# 2. 安裝
make install

# 3. 驗證
make test          # 47/47 應全綠（不需 API key）
```

---

## 1. 跑你的第一個任務

```bash
make demo
```

等價於：

```bash
python -m devswarm --task-file specs/profit_calc.md --verbose
```

### 預期看到的時序

```
╭──────────── DevSwarm starting ────────────╮
│ task_id: a3f2c1b9                          │
│ workspace: /...workspace/a3f2c1b9          │
│ models: pm=claude-opus-4-7 architect=...   │
│ max heal: 5                                │
╰────────────────────────────────────────────╯
  → 🧭 PM         PRD generated (2843 chars)   tok in=312 out=921 cache_read=0 (0%) $0.0763 11.2s
  → 🏛 Architect  Arch spec ... 9 constraints  tok in=2410 out=684 cache_read=0 (0%) $0.0875 9.7s
  → ⌨ Coder       iter=1, steps=2, files=2     tok in=4112 out=2103 cache_read=2987 (42%) $0.0408 14.5s
  → 🧪 QA         pytest PASSED (exit=0)       tok in=0 out=0 cache_read=0 (0%) $0.0000 0.6s

╭──────────── DevSwarm run summary ──────────╮
│ status: PASSED ✅                           │
│ task_id: a3f2c1b9                           │
│ heal iterations used: 1                     │
│ total cost (est.): $0.2046 USD              │
│ artifacts written: 2                        │
╰────────────────────────────────────────────╯
                Artifacts
  path                              kind  bytes
  real_profit_calculator.py         code  4287
  test_real_profit_calculator.py    test  3941
```

預期耗時：30-90 秒。預期成本：**USD $0.15 - $2.50**（依模型 + 任務複雜度）。

成品路徑：`workspace/<task_id>/real_profit_calculator.py` + `test_real_profit_calculator.py`

---

## 2. 自我修復迴圈會長這樣

當第一輪 Coder 寫的代碼測試沒過：

```
  → ⌨ Coder       iter=1, steps=2, files=2     ... $0.0408 14.5s
  → 🧪 QA         pytest FAILED (exit=1, 3 failing) ... $0.0034 2.1s
  → ⌨ Coder       iter=2, steps=3, files=1     tok in=... cache_read=4892 (78%) $0.0291 11.8s
  → 🧪 QA         pytest PASSED (exit=0)       $0.0000 0.6s
```

關鍵觀察：
- **`heal_iter` 用了 2 次**，最多 5 次
- **iter=2 的 `cache_read` 比例高（78%）** — system prompt 命中快取，成本砍 90%
- **QA 在 PASS 時 cost=0** — 沒有 LLM call，純機械判斷

如果 5 輪都沒過 → 退出碼 1、印出最後一次 QA 報告（root_cause + fix_direction）。這時候：

```bash
# 看蜂群留下了什麼
ls workspace/<task_id>/
cat workspace/<task_id>/test_real_profit_calculator.py

# 自己跑一次測試看詳細失敗
cd workspace/<task_id>
python -m pytest -v

# 看 task.json 找出原始需求
cat task.json
```

通常失敗原因（按頻率排）：
1. **Decimal vs float 型別不一致** — 修一個地方就解
2. **AC 寫得太模糊**，Coder 自行解讀錯方向 — 修 spec、重跑
3. **Pydantic 嚴格模式拒絕測試假資料** — 測試裡用了 float literal，Coder 沒處理；修 spec 加說明

---

## 3. 成本控制

### 預算上限建議
- 單一任務：**USD $5**（超過時自己中斷檢查）
- 單日總額：**USD $30**（T1 全程 < $30 是目標）
- 單月總額：**USD $50**（含開發 + 真實營運）

### 為什麼會超預算
1. **System prompt 沒命中快取** — 第一次跑都會 miss，後續 5 分鐘內同任務命中
2. **Coder 進入無限工具循環** — 已設 `max_steps=8`，理論上不會。若觀察到 `steps=8` 終止要警惕
3. **過多 heal 迭代** — 預設 `max_heal_iters=5`。若觀察到一直 fail 又被自動修，spec 可能有矛盾，停下手動 review

### 看 cache hit 率
- Coder 第 2-5 輪 `cache_read` 比例應 > 60%
- 若 < 30%：system prompt 變動了或超過 5 分鐘 TTL → 連跑兩個任務縮短間隔

---

## 4. 跑你自己的任務

```bash
# 從檔案
make swarm REQ="$(cat specs/uniform_invoice_validator.md)"

# 或直接命令列
python -m devswarm "Build a Pydantic model that validates Taiwan 行動電話載具 (3-15 char, starts with /, alphanumeric + hyphen + period). Include 8 tests covering valid/invalid cases and the edge of being exactly the minimum/maximum length."
```

### 寫好任務簡報的 5 個準則
1. **單檔輸出**：說清楚這是「一個 .py 模組 + 一個 test_<name>.py」
2. **公開介面寫死**：`def foo(input: Input) -> Output` 連型別都列出來
3. **AC ≥ 10 條**：每條都要能變成 pytest 斷言（含具體數字）
4. **Out of scope 列出來**：防止 Coder 過度發揮
5. **依賴限定 stdlib + pydantic**：不要讓蜂群裝新套件

範本見 `specs/profit_calc.md` 或 `specs/uniform_invoice_validator.md`。

---

## 5. 把蜂群產出物搬進正式 repo

DevSwarm 產出在 `workspace/<task_id>/`（gitignored），你需要手動搬進來：

```bash
# 1. 看蜂群產出了什麼
ls workspace/<task_id>/

# 2. 搬進 restaurant_api/services/
mkdir -p restaurant_api/services
cp workspace/<task_id>/real_profit_calculator.py restaurant_api/services/
cp workspace/<task_id>/test_real_profit_calculator.py tests/services/

# 3. 確認測試還在 restaurant_api 環境也通過
.venv/bin/python -m pytest tests/services/

# 4. commit
git add restaurant_api/services/ tests/services/
git commit -m "feat(services): import real_profit_calculator from DevSwarm task <task_id>"
```

未來會做：自動 promotion 腳本（`make promote TASK=<task_id> DEST=restaurant_api/services/`）。現在手動。

---

## 6. 常見錯誤排查

### `ANTHROPIC_API_KEY not set`
→ `cp .env.example .env`、填 key、重跑

### `langgraph` import 警告
無害，是 LangGraph 套件自己的廢棄通知。功能正常。

### `pytest exceeded 60s timeout`
代表蜂群產出的測試自己跑超過 60s（不太可能除非寫到無限迴圈）。
解：`DEVSWARM_SANDBOX_TIMEOUT=120 make demo`

### `recursion_limit reached`
LangGraph 預設 25，我們已設 64。理論上夠 6 輪 heal。若達到：spec 有死循環邏輯，先檢查 AC 是否互相矛盾。

### `Workspace path escapes` — 防護觸發
Coder 試圖寫絕對路徑或 `..`。會被 `WorkspaceManager` 擋。
這是設計如此的安全邊界，不要關。

### `503` from /health endpoint
DB 沒起。`sudo service postgresql start` 或 `make db-up`（後者需 docker）。

---

## 7. 進階：兩個 Agent 一起跑

DevSwarm 目前是單任務 CLI，但你可以開兩個 terminal：

```bash
# Terminal 1
make swarm REQ="..."

# Terminal 2（同時）
make swarm REQ="..."
```

兩個任務寫到不同 `workspace/<task_id>/`，不衝突。
**但會共享 Anthropic rate limit**。先互不干擾單跑驗證 ok 再並行。

---

## 8. 安全紅線

1. **不要把 DevSwarm 指向有 prod 憑證的環境**。生成的代碼在 subprocess 跑，rlimit 只是緩衝不是邊界。
2. **不要把 `workspace/` 加進 git**（已 gitignored）。任務內容可能含敏感業務邏輯。
3. **不要把 `.env` 加進 git**（已 gitignored）。
4. **Coder 永遠不能上網**。tool 集只有 `write_file` / `read_file` / `list_files`，沒有 `curl` / `web_search`。
5. **沙盒 60 秒超時不是性能限制是 DoS 防護**。不要為了讓特定任務通過就調太大。

---

## 9. 一個任務從零到合併的流程

```
你寫 spec/foo.md  →  make swarm REQ=...           →  workspace/<id>/ 產出
                          │                                  │
                          ▼                                  ▼
                  PM → Arch → Coder → QA            review 產出，跑 pytest
                  ↑                                          │
                  └── self-heal up to 5 ──┐                 │
                                          │                 ▼
                                          ▼          手動搬進 restaurant_api/services/
                                       pass → END    + tests/services/
                                                            │
                                                            ▼
                                                    git commit, push, merge
```

T1 預計 6 個 calc 模組會經過這個流程。一週應能跑完 3-4 個（review + 整合的人力工是瓶頸，不是蜂群速度）。
