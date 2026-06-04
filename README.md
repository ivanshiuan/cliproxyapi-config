# Autonomous RestTech Enterprise

[![CI](https://github.com/ivanshiuan/cliproxyapi-config/actions/workflows/ci.yml/badge.svg?branch=claude%2Fautonomous-resttech-enterprise-oW9jp)](https://github.com/ivanshiuan/cliproxyapi-config/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-Proprietary-red)

> **台灣全域 AI 餐飲智慧營運作業系統**。雙層架構：
> - **DevSwarm**（LangGraph 4-agent 蜂群）→ 自動產出程式碼
> - **RestSwarm**（FastAPI + 25 表 PG + LINE）→ 真實餐飲後端

## 文件導覽

| # | 文件 | 用途 |
|---|---|---|
| **入口** | [CLAUDE.md](CLAUDE.md) | 專案說明書、不變法則 |
| **指揮官** | [COMMANDER_HANDOFF.md](COMMANDER_HANDOFF.md) | 你還沒做的事 |
| **CHANGELOG** | [CHANGELOG.md](CHANGELOG.md) | 版本歷史 |
| 00 | [docs/00_vision.md](docs/00_vision.md) | 願景凍結（SSOT） |
| 01 | [docs/01_tech_stack_recommendation.md](docs/01_tech_stack_recommendation.md) | 技術選型 |
| 02 | [docs/02_devswarm_architecture.md](docs/02_devswarm_architecture.md) | 蜂群架構 |
| 03 | [docs/03_roadmap.md](docs/03_roadmap.md) | Phase 0→5 路線圖 |
| 04 | [docs/04_data_schema.md](docs/04_data_schema.md) | 909 行 PostgreSQL DDL |
| 06 | [docs/06_execution_plan.md](docs/06_execution_plan.md) | 12 任務拆解 |
| 07 | [docs/07_devswarm_runbook.md](docs/07_devswarm_runbook.md) | DevSwarm 操作手冊 |
| 08 | [docs/08_safety_compliance.md](docs/08_safety_compliance.md) | 食安/勞檢/個資/災難 SOP |
| 09 | [docs/09_phase1_extension_kit.md](docs/09_phase1_extension_kit.md) | KDS / 訂位 / LINE |
| 10 | [docs/10_claude_code_workflow.md](docs/10_claude_code_workflow.md) | Claude Code 工作流 |
| 11 | [docs/11_production_deployment.md](docs/11_production_deployment.md) | 部署 SOP + T-7 開店 |

---

## DevSwarm — AI Agent 蜂群開發工廠

> DevSwarm 是一套 LangGraph 多 Agent 蜂群，會把指揮官的需求一句話翻譯成可運行、有測試、自我修復過的 Python 模組。後續整套餐飲系統 — ERP、CRM、行銷、地圖、人事 — 都會由它孵出來。

---

## 它是什麼

四個 Agent，用模型成本對應職責，自我修復直到測試通過：

```
START
  │
  ▼
[ PM Agent (Opus 4.7) ] ──► PRD + 公開介面 + 驗收標準
  │
  ▼
[ Architect Agent (Opus 4.7) ] ──► 架構規格 + 資安/正確性約束
  │
  ▼
[ Coder Agent (Sonnet 4.6) ] ──► 寫 module.py + test_module.py
  │
  ▼
[ QA Agent (Haiku 4.5) ] ──► 跑 pytest，回報 pass/fail
  │
  ├── tests_passed ──► END (✅)
  │
  └── tests_failed && heal_iter < max ──► 回到 Coder，附上錯誤報告
```

預設 `max_heal_iters = 5`。

## 快速開始

### 1. 安裝

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. 設定 API Key

```bash
cp .env.example .env
# 編輯 .env，填入 ANTHROPIC_API_KEY
```

### 3. 跑示範任務（真實損益計算引擎）

```bash
python -m devswarm --task-file specs/profit_calc.md --verbose
```

預期：DevSwarm 會在 `workspace/<task_id>/` 產出 `real_profit_calculator.py` 與 `test_real_profit_calculator.py`，pytest 全綠則 exit code 0。

### 4. 自訂任務

```bash
python -m devswarm "Build a Pydantic model that validates Taiwan 統一編號 (8-digit business tax ID) with the official checksum algorithm. Include 8 test cases covering valid/invalid IDs and edge cases."
```

## 倉庫結構

```
.
├── devswarm/                # 蜂群本體
│   ├── cli.py               # 入口
│   ├── graph.py             # LangGraph 拓撲
│   ├── state.py             # SwarmState
│   ├── config.py            # 模型選擇 / 上限 / 路徑
│   ├── llm.py               # Anthropic SDK wrapper（prompt caching + tool use）
│   ├── workspace.py         # 沙盒檔案系統
│   ├── sandbox.py           # pytest subprocess runner
│   ├── prompts/             # 四個 Agent 的系統提示
│   └── nodes/               # 四個 Agent 的執行邏輯
│
├── docs/                    # 願景、技術選型、架構、Schema、Roadmap
│   ├── 00_vision.md
│   ├── 01_tech_stack_recommendation.md
│   ├── 02_devswarm_architecture.md
│   ├── 03_roadmap.md
│   └── 04_data_schema.md
│
├── specs/                   # DevSwarm 接受的任務簡報
│   └── profit_calc.md
│
├── tests/                   # DevSwarm 自身的單元測試
├── workspace/               # 蜂群產出物（gitignored）
│
├── pyproject.toml
├── requirements.txt
├── .env.example
└── README.md
```

## 開發/測試 DevSwarm 自己

```bash
pytest                       # 不需要 API key，只測管線
pytest -m integration        # 需要 API key，會真的呼叫 Anthropic
```

## 設計哲學

- **撐住一輪自我修復就是勝利。** Phase 0 只追求「四個 Agent + 自癒迴圈能跑通」，不堆抽象。
- **模型分層用錢買對位置：** 規劃用 Opus，碼農用 Sonnet（呼叫 ≤5 次／任務），驗收用 Haiku（高頻、機械式）。
- **Prompt caching 不是 optimization，是預算紅線。** 所有系統提示都標 `cache_control: ephemeral`；自癒迴圈第 2-5 次都吃到快取。
- **沙盒不是安全邊界。** v1 用 subprocess + rlimit；生產自託管時請套 Docker/firejail。**不要把這個工具指向有 prod 憑證的環境。**

## 後續路徑

依 `docs/03_roadmap.md`：

| Phase | 目標 |
|---|---|
| **0（現在）** | DevSwarm 骨架可跑通 demo |
| **1** | 用 DevSwarm 把單店 MVP（ERP/BOM/真實損益/招待精算/打卡）孵出來 |
| **2** | CRM + 行銷 |
| **3** | Google 地圖 + 區域數據 |
| **4** | 連鎖 / 加盟 / 總部 |
| **5** | 三大閉環自主運行 |

## License

Proprietary — 指揮官自有。
