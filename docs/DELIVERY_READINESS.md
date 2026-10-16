# 交付就緒度（2026-10-14 實測）

> 這份是**一次性、有日期的結論**，每一條都附「怎麼驗證」。會隨時間過期的數字（測試數、表數、API 數）不寫在這裡，
> 請看機器產生的 [`docs/STATUS.md`](STATUS.md)。PR 與分支的完整盤點見 [`docs/BRANCH_AND_PR_LEDGER.md`](BRANCH_AND_PR_LEDGER.md)。

## 一句話

**預設分支上的餐飲後端是真的、跑得起來；但它不是預定 10/13 交付的版本。** POS（PR #29）與 #31 到 10/14 仍是 open、
沒有合併；預設分支頭停在 9/16。我把「預設分支 + #31 + #29 + 本 PR」合在一起實測過，結果可用（見下表）。
**「老闆今天真正賺多少」這個核心賣點，即使全部合併也還沒有：真實損益引擎沒有接到任何地方。**

## 已驗證（在本機空環境重建後實際執行）

| 項目 | 結果 | 怎麼重現 |
|---|---|---|
| 從空環境重建 | 1 分 49 秒；10 個 migration 在真的 Postgres 16 + pgvector 套用完 | `make bootstrap` |
| 品質閘門（本分支） | ruff ✅ · pyright 0 錯誤 ✅ · 458 通過 / 1 跳過 · alembic 無漂移 · DB smoke ✅ | `make full-check` |
| TIGER LINE 測試（獨立 gate） | 181 通過 | `make tigerline-check` |
| 一日流程 | 打卡 → 開單 → 結帳 → 報廢 → 員工餐 → 下班工時，exit 0 | `make seed && make demo-flow` |
| **整合實驗**：預設分支 + #31 + #29 + 本 PR | 合併**零衝突**；Alembic **單一 head**（`b7d2e9a41f05`）；20 個 migration 安全掃描通過；ruff / pyright 0 錯誤；**625 通過 / 1 跳過 / 0 失敗**；OpenAPI 118 paths | 在隔離 worktree 依序 `git merge` 四個 head，用獨立資料庫跑同一組 gate |
| 合併順序 | `#31 → #29` 與 `#29 → #31` 兩種都無衝突 | 同上 |
| CI 現在會在「合併結果」上跑 | 本 PR 的 push 同時觸發了 `pull_request` 與 `push` 兩種 CI | `.github/workflows/ci.yml` |

唯一的跳過是 `tests/test_reviewer_eval.py`：需要 `ANTHROPIC_API_KEY` 的 live LLM 評測（刻意 opt-in）。

## 本輪修掉的問題

| 問題 | 根因 | 修法 |
|---|---|---|
| pyright 一夜變紅（`expiry_warning.py` 2 個錯誤） | ruff / pyright 不在任何依賴檔、沒鎖版；SQLAlchemy 又升到 2.1 | `requirements-dev.txt` 精確鎖版；程式碼加顯式 NULL 守衛 |
| `make full-check` 在乾淨機器第一步就失敗 | `make install` 不裝 ruff | `make install` 改為連同鎖版工具一起裝 |
| 開發環境重建不了 | 沒有 venv / 沒有 `resto` 角色與資料庫 / 本機 Postgres 缺 pgvector | `scripts/bootstrap_env.sh`（`make bootstrap` / `make env-check`），冪等 |
| 分眾推播繞過依賴注入 → 測試會打真的 LINE | `routers/membership.py` 直接呼叫 `get_messenger()` | 與 #31 逐位元相同的修法（先合哪個都不衝突） |
| **測試套件帶著真憑證就會對真實客人推播** | 測試沒有隔離 `LINE_CHANNEL_*` | `conftest.pytest_configure` 清空憑證 + 回歸測試 `tests/test_hermetic_env.py` |
| 4 個測試在有 seed 資料的 DB 上失敗 | 數整張表的列數，違反「測試要 scope 到自己的 store」 | 改為 `where store_id == seed_store.id` |
| CI 從沒在合併結果上跑 | `pull_request` 只列不存在的 `main` | 加上預設分支 |
| 下注測試混在餐飲的綠燈裡 | 同一個 `pytest tests/` | 拆成獨立 job / `make tigerline-check`（程式碼未動） |
| 文件裡的數字互相矛盾（測試 47/274/304/398、表 18/25/33） | 手寫數字會漂移 | `make status-report` 從 checkout 產生 `docs/STATUS.md` |

## 還沒做到 / 無法從這裡驗證（請不要把它當成已完成）

1. **真實損益沒有接出來。** `compute_daily_pnl`（損益引擎）與 `validate_uniform_invoice`（統編檢核）在應用程式碼中**零呼叫點**
   （`docs/STATUS.md` 有每次重新計算的表）。#29 的 `/reports/*` 全是**銷售分析**（渠道、時段、熱銷品、總部），不是損益。
   資料其實夠組出輸入（`OrderLine.cogs_actual/cogs_theoretical`、折扣、報廢、試吃、工時分桶、時薪），
   缺的是**固定成本、平台抽成、營業稅處理、薪資算法**——這些要老闆決定，我不替你猜（見決策登記簿）。
2. **POS 的畫面我沒有用瀏覽器開過。** 625 個測試是 API 層級；#29 的 10 幾個前端頁（POS 終端、KDS、自助點餐、QR 點餐、叫號板…）
   沒有端到端的人工驗收。交付前至少要在店裡用實機走一遍。
3. **#29 是 114 檔、+18,680 行、42 個 commit，沒有任何人類 review**（只有 CodeRabbit）。
4. **DevSwarm 的 live LLM 路徑從沒驗證過**（沒有 API key）。
5. 真實硬體（出單機、錢箱、標籤機）、真實 LINE OA、電子發票字軌、壓力測試、備份還原演練——都不在這輪能驗證的範圍。

## 合併時的陷阱（細節在台帳）

- **Alembic 多頭**：5 條 PR 鏈 + 2 條無 PR 分支鏈的 `down_revision` 全指向現行 head。合第一個後，其餘必須改接。
- `tests/test_restaurant_api.py` 寫死表數；每個加表的 PR 都會在同幾行衝突，合併後數字要重算。
- 無 PR 的分支 `restaurant-system-competitor-analysis` 與 #29 建了同名資料表，兩者都套用會 `DuplicateTable`。

## 另有一項資安事項

有一項需要**輪替憑證**的事項已私下回報創辦人。細節刻意不寫進這個公開 repo。
