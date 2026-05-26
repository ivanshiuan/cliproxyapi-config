# 指揮官晨報 — 隔夜開發成果速覽

> 一句話：**DevSwarm 蜂群已建成、Phase 1 餐飲後端骨架已就位、47 個測試全綠、CI 已配置、可立刻 `make demo` 跑第一個 AI 自動產出的真實損益模組。**

---

## 一分鐘看完

| 項目 | 狀態 |
|---|---|
| Phase 0 — DevSwarm AI 蜂群骨架 | ✅ **完成可用** |
| Phase 1 — 餐飲後端骨架（FastAPI + 18 表） | ✅ **完成可用** |
| 完整文件（願景 / 技術選型 / 架構 / Schema / Roadmap） | ✅ **完成** |
| 第一個 demo 任務（真實損益計算引擎）規格 | ✅ **完成** |
| 測試（47 個，含 mocked 端到端） | ✅ **全綠** |
| CI（GitHub Actions） | ✅ **已配置** |
| Lint（ruff） | ✅ **全綠** |
| Git push | ✅ **已推上 `claude/autonomous-resttech-enterprise-oW9jp`** |

---

## 你早上的三個動作

### 1. 看一遍 repo 結構（10 秒）

```bash
make status
```

### 2. 跑測試確認沒爆（10 秒）

```bash
make test
```

預期：47 passed。

### 3. 餵 DevSwarm 跑第一個真實任務（5-10 分鐘 + USD 約 1-3 元）

需要先在 `.env` 填入 ANTHROPIC_API_KEY：

```bash
cp .env.example .env
# 編輯 .env，填入 ANTHROPIC_API_KEY=sk-ant-...
make demo
```

預期：
- PM Agent → Architect Agent → Coder Agent → QA Agent → END
- 產出 `workspace/<task_id>/real_profit_calculator.py` + `test_real_profit_calculator.py`
- pytest 全綠（如沒過會自動 heal 最多 5 次）
- 終端列出 cost、token usage、cache hit 率

**第一次跑會花較多錢**（沒有 prompt cache）；之後同樣的 system prompt 5 分鐘內快取命中，便宜 90%。

---

## 完整目錄樹

```
/home/user/cliproxyapi-config
│
├── README.md                       # 入口、快速啟動
├── MORNING_BRIEF.md                # 你正在看
├── Makefile                        # make help 看全部指令
├── pyproject.toml / requirements.txt
├── .env.example                    # 填 ANTHROPIC_API_KEY
├── .github/workflows/ci.yml        # 自動跑 ruff + pytest
│
├── docs/                           # 5 份戰略文件，2900+ 行
│   ├── 00_vision.md                # 願景凍結（SSOT）
│   ├── 01_tech_stack_recommendation.md  # FastAPI+Next.js+PG 為何
│   ├── 02_devswarm_architecture.md # 蜂群架構手冊
│   ├── 03_roadmap.md               # Phase 0→5 時程、成本、KPI
│   └── 04_data_schema.md           # 909 行 Postgres DDL（含真實損益物化視圖）
│
├── specs/                          # DevSwarm 接受的任務簡報
│   └── profit_calc.md              # 第一個 demo：15 個驗收標準
│
├── devswarm/                       # AI 蜂群本體（2500+ 行）
│   ├── cli.py / __main__.py        # python -m devswarm 入口
│   ├── graph.py                    # LangGraph 拓撲 + self-heal 條件邊
│   ├── state.py                    # SwarmState
│   ├── config.py                   # 模型選擇 / 上限 / 路徑 / 計價
│   ├── llm.py                      # Anthropic SDK wrapper（prompt cache + tool use）
│   ├── workspace.py                # 沙盒檔案系統（path-traversal 防護）
│   ├── sandbox.py                  # pytest subprocess + rlimits + timeout
│   ├── prompts/                    # 四 Agent 系統提示
│   │   ├── pm.py                   # PRD 產生器（Opus 4.7）
│   │   ├── architect.py            # 架構 + 資安約束（Opus 4.7）
│   │   ├── coder.py                # 程式碼產生器（Sonnet 4.6）+ heal template
│   │   └── qa.py                   # JSON 診斷產生器（Haiku 4.5）
│   └── nodes/                      # LangGraph 節點實作
│       ├── pm.py
│       ├── architect.py
│       ├── coder.py                # 含 write_file/read_file/list_files 工具
│       └── qa.py                   # pytest 通過時跳過 LLM，省錢
│
├── restaurant_api/                 # Phase 1 餐飲後端（2200+ 行）
│   ├── main.py                     # FastAPI app（/health 含 DB ping + 503）
│   ├── config.py                   # Pydantic Settings v2
│   ├── database.py                 # async SQLAlchemy 引擎、會話、健康探針
│   ├── docker-compose.yml          # Postgres 16 + Redis 7（開發用）
│   ├── alembic.ini + alembic/env.py # async-aware 遷移系統（可 autogenerate）
│   ├── initdb/01_extensions.sql    # uuid-ossp、citext、pg_trgm
│   ├── models/                     # 18 表 SQLAlchemy 2.x ORM
│   │   ├── base.py                 # Base + Mixins + uuid7() + Money 型別
│   │   ├── tenants.py / stores.py / employees.py
│   │   ├── menu.py                 # MenuCategory、MenuItem（含 POS 預留）
│   │   ├── inventory.py            # Ingredient、Recipe、StockMovement（append-only）
│   │   ├── orders.py               # 訂單 4 表（含 統一發票 / 載具 / 統編）
│   │   ├── cost_events.py          # 報廢 / 員工餐 / 試吃 — 隱藏成本三巨頭
│   │   └── hr.py                   # 排班、打卡（4 段勞基法工時）、請假
│   └── README.md
│
├── tests/                          # 47 個測試
│   ├── test_state.py
│   ├── test_workspace.py           # 路徑遍歷防護 / 寫讀往返
│   ├── test_sandbox.py             # pytest subprocess 行為
│   ├── test_imports.py             # 全模組可載入 / prompt 達快取門檻
│   ├── test_graph_mock.py          # ⭐ Mocked 端到端：happy / self-heal / max-heal
│   └── test_restaurant_api.py      # FastAPI smoke、18 表存在、money 欄位驗證
│
└── workspace/                      # DevSwarm 產出物（gitignored）
```

---

## 關鍵設計決策（你可能會被問）

### 為何 LangGraph 不是自寫 router？
有條件邊、有迴圈、有狀態合併規則。LangGraph 把這些都做好了；自寫等於重新發明輪子。

### 為何 PM/Architect 用 Opus 4.7，Coder 用 Sonnet，QA 用 Haiku？
**錢買對位置：**
- PM/Architect 各只呼叫一次，但決定整個任務品質 → 用最強的
- Coder 在 self-heal 迴圈最多呼叫 5 次 + tool use 每次又多輪 → 用中等且擅長 code
- QA 主要是機械式判讀（pytest exit code 已決定 pass/fail），LLM 只需要在失敗時診斷 → 用最便宜

### 為何 prompt caching 是「預算紅線」不是「優化」？
Self-heal 第二輪起，同一個 Coder system prompt 重複用。命中快取後 input cost 砍 90%。一個任務跑 5 輪沒有 cache 等於 5 倍成本。

### 為何 UUIDv7 不是 BIGSERIAL？
- 時間排序友好（同一日的 ID 自然聚集）
- 無中央自增瓶頸（連鎖加盟多店時關鍵）
- 跨 DB / sharding 不衝突

### 為何 stock_movements append-only？
庫存事件溯源（event sourcing）。永遠可以從 ledger 重建 `inventory_snapshots`，找出「為什麼今天少了 5 公斤高麗菜」一查到底。

### 為何 Money = Numeric(14, 4) 不是 Numeric(10, 2)？
4 位小數是為了**單位成本**（一公克 0.1234 元的精度需要）。最終 TWD 顯示時再 quantize 到 2 位。永不踩到浮點誤差。

---

## 已知限制（誠實揭露）

1. **沙盒不是真正的安全邊界。** 產生的程式碼在 subprocess + CPU/AS rlimit 跑，沒套 Docker / firejail。**不要把 DevSwarm 指向有 prod 憑證的環境。**
2. **無 Alembic 初始遷移檔。** 第一次跑 `alembic revision --autogenerate -m "init"` 才會產生。需要先 `make db-up`。
3. **無 LangGraph checkpointer。** 任務失敗無法 `--resume`，得重跑。Phase 0 可接受。
4. **Coder 只會寫 Python。** 前端 / TypeScript 代碼生成是 Phase 1 中後段才加的能力。
5. **無認證、無多租戶、無前端。** 全部排到 Phase 2+，與 `docs/03_roadmap.md` 一致。

---

## 本週建議節奏

| 天 | 行動 |
|---|---|
| **今天（醒來後）** | `make test` 確認、`make demo` 跑第一個任務、檢視 `workspace/<task_id>/` 產出 |
| **明天** | 想 2-3 個你日常會用到的小模組，寫成 `specs/*.md`，餵給 DevSwarm 自動產生 |
| **後天** | `make db-up` 啟動 Postgres → `alembic revision --autogenerate -m init` 產出初始遷移 → `alembic upgrade head` → 跑 `make api` 看 /docs |
| **本週末** | 整理 demo 任務跑出來的程式碼，挑一兩個搬進 `restaurant_api/` 當業務邏輯起點 |
| **下週** | 開始用 DevSwarm 產出 BOM 扣料 service、訂單 router、報廢事件 router |

---

## 下一個推進點（如果你想我繼續）

我建議的下兩件高 ROI 任務：

1. **第二個 demo 任務**：`specs/uniform_invoice_validator.md` — 台灣統一編號（8 碼商業稅籍）官方驗證演算法。實用、簡單、能驗證 DevSwarm 可重用性。已知會用在 `Order.buyer_tax_id`。

2. **POS 訂單 service 規格**：`specs/orders_service.md` — POST /orders 流程含 BOM 扣料、發票欄位寫入、招待/折扣分流計帳。這是 Phase 1 最大塊的業務邏輯，產出後直接接到 restaurant_api。

如果你想我直接繼續，給我訊號（一句話即可），我接著開幹。
