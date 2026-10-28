# 交付就緒度（2026-10-14 實測；2026-10-27 更新）

> 這份是**一次性、有日期的結論**，每一條都附「怎麼驗證」。會隨時間過期的數字（測試數、表數、API 數）不寫在這裡，
> 請看機器產生的 [`docs/STATUS.md`](STATUS.md)。PR 與分支的完整盤點見 [`docs/BRANCH_AND_PR_LEDGER.md`](BRANCH_AND_PR_LEDGER.md)；
> 待老闆決定的事見 [`docs/DECISIONS.md`](DECISIONS.md)。

## 一句話（10/27）

**#40、#31、#29 已在 10/26 依序合併進預設分支**（merge commit `1fa4bb7`、`c60b8ec`、`f3edb61`）：POS 全套、LINE 推播修復、
每日損益報表現在都在主幹上。合併後的預設分支實測：單一 Alembic head、測試全過、GitHub CI 通過（見下表）。
**合併不等於上線**：部署到店（`docs/24`）、真實金額與密碼的設定，都還要老闆動手。

**「老闆今天真正賺多少」**：`GET /reports/daily-pnl`，並且與 POS 結帳台逐分對帳。
要把固定成本金額與雇主負擔費率設進去才算完整，用法見 [`docs/DAILY_PNL.md`](DAILY_PNL.md)。

**試營運 10/27（今天）。** 現在最大的風險不在程式碼：POS 畫面沒有人在店裡實機走過、統一發票怎麼開還沒決定（D15），
還有正式環境的管理員密碼必須先設（沒設 api 會直接起不來，見下方）。

## 已驗證（在本機空環境重建後實際執行）

| 項目 | 結果 | 怎麼重現 |
|---|---|---|
| 從空環境重建（10/14） | 1 分 49 秒；10 個 migration 在真的 Postgres 16 + pgvector 套用完 | `make bootstrap` |
| 品質閘門（10/27，管理員密碼防呆的分支） | ruff ✅ · pyright 0 錯誤 ✅ · **708 通過 / 1 跳過** · alembic 無漂移 · DB smoke ✅ | `make full-check` |
| TIGER LINE 測試（獨立 gate，10/14） | 181 通過（程式碼未動） | `make tigerline-check` |
| 一日流程（10/14） | 打卡 → 開單 → 結帳 → 報廢 → 員工餐 → 下班工時，exit 0 | `make seed && make demo-flow` |
| **合併後的預設分支（10/26，`f3edb61`）** | 在乾淨 checkout + 全新資料庫重跑：ruff ✅ · pyright 0 錯誤 ✅ · 單一 Alembic head `b7d2e9a41f05`、遷移套用、無漂移 · **678 通過 / 1 跳過 / 0 失敗**；GitHub 主幹 CI（push）全綠 | 在 `f3edb61` 的 worktree 用獨立資料庫跑同一組 gate |
| **損益 vs 結帳台對帳（10/22）** | 用 #29 的真實結帳流程下 4 張單（現金、拆單分次付款、%折扣、剛好差半分的進位邊界）→ 報表應收 972.37 = 收款單合計 972.37，**差 0.00**；服務費、淨銷售額也與 #29 自己的 `/reports/sales` 一致 | 一次性對帳；常駐把關 `tests/test_daily_pnl_till_parity.py`（#29 合併後 11 個案例真的在跑，已通過） |
| 合併順序 | `#31 → #29` 與 `#29 → #31` 兩種都無衝突（10/14 實測）；實際採 `#40 → #31 → #29` 並全部乾淨合併（10/26） | 同上 |
| 再加上 #45（Odoo）（10/22） | 唯一文字衝突是寫死表數的 `tests/test_restaurant_api.py`（台帳已記）；migration 另需改接 head | 在合併樹上 `git merge --no-commit` 後 `--abort` |
| CI 在「合併結果」上跑 | PR 與 push 兩種 CI 都會跑 | `.github/workflows/ci.yml` |

預設分支上唯一跳過的測試：`tests/test_reviewer_eval.py`（需要 `ANTHROPIC_API_KEY` 的 live LLM 評測，刻意 opt-in）。

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
| **真實損益算好了卻沒人呼叫**（`compute_daily_pnl` 零呼叫點） | 引擎寫好、測過，從沒接到任何路由 | `GET /reports/daily-pnl`：逐張訂單結算折扣、含稅營收、10% 服務費、人事（讀員工表）、固定成本、收款對帳 |
| 損益的服務費與結帳台差 1 分 | 結帳台用銀行家進位，報表用四捨五入；遇到剛好差半分就不同 | 改成與結帳台一致，並以 #29 的 `amount_due` 逐案對帳（合併後常駐） |
| 損益報表在同一個 session 內讀到過期的收款 | 已載入的訂單不會重讀新寫入的收款 | 報表查詢強制讀資料庫當下的列，回歸測試會先失敗 |
| 損益設定（`RESTO_PNL_*`）手改寫錯會變成沒有訊息的 500 | 全形引號、最後多逗號、17% 寫成 17、負數金額 | 驗證範圍並回 503，直接指名是哪個變數、怎麼改 |
| **正式環境可以用預設的管理員密碼上線**（後台含損益報表等於對全世界開放）（10/27） | 通行碼與 cookie 密鑰的預設值就寫在公開 repo；沒有任何機制要求覆蓋 | api 啟動時檢查：非開發環境若兩者仍是預設／範例文字或太短（通行碼 < 8、密鑰 < 32 字元）就**拒絕啟動**並說明原因；production compose 強制 `RESTO_ENV=prod`；用真的 uvicorn 行程驗證兩個方向 |
| STATUS 把沒人呼叫的引擎標成「已接線」 | 用文字比對，連 import、docstring 與腳本自己的對照表都算 | 改掃 AST 的實際呼叫；回歸測試 |
| 決策登記簿與 PR #29 改了同一行（`COMMANDER_HANDOFF.md`） | 兩邊都改了「最後一次重寫」那一行 | 本 PR 不再動該檔，登記簿搬到新檔 `docs/DECISIONS.md` |

## 還沒做到 / 無法從這裡驗證（請不要把它當成已完成）

1. **統一發票沒有人在開。** POS 只「登記」統編與載具，沒有接財政部電子發票；統編檢核引擎（`validate_uniform_invoice`）
   仍然零呼叫點，連 #29 合併後也一樣（`docs/STATUS.md` 每次重算）。試營運當天就會碰到，見 D15。
2. **POS 的畫面我沒有用瀏覽器開過。** 測試是 API 層級；#29 的 10 幾個前端頁（POS 終端、KDS、自助點餐、QR 點餐、叫號板…）
   沒有端到端的人工驗收。交付前至少要在店裡用實機走一遍。
3. **#29 是 114 檔、+18,680 行、42 個 commit，沒有任何人類 review**（只有 CodeRabbit）。
4. **損益報表還缺老闆要給的輸入**（D12–D14）：固定成本金額、雇主負擔費率；折舊與稅務不在內；服務費假設全歸公司。
   報表的「警告」欄會逐項講，不會假裝算完整。（服務費欄位來自 #29，已合併，報表現在讀得到。）
5. **DevSwarm 的 live LLM 路徑從沒驗證過**（沒有 API key）。
6. 真實硬體（出單機、錢箱、標籤機）、真實 LINE OA、壓力測試、備份還原演練——都不在這輪能驗證的範圍。
7. **`restaurant_api/main.py` 的「路徑已存在就不再掛載」守衛已失效**：FastAPI 0.14x 起 `app.routes` 裡是沒有 `.path` 的包裝物件，
   所以守衛永遠判斷「不存在」。目前無害（只造成 12 個 Duplicate Operation ID 警告）；新增的路由因此改用不依賴它的掛載方式。
8. **上線前一定要設 `RESTO_ADMIN_PASSCODE` 與 `RESTO_SESSION_SECRET`**（`openssl rand -hex 32`）：沒設、或還是預設／範例值，api 會直接起不來
   （這是設計，見 `docs/11` §2）。`docker logs resto-api` 會寫出是哪一個。

## 之後要合併其他 PR 時的陷阱（細節在台帳；#40、#31、#29 已合併）

- **Alembic 多頭**：預設分支現在的單一 head 是 `b7d2e9a41f05`；其餘 5 條 PR 鏈 + 2 條無 PR 分支鏈的 `down_revision` 仍指向舊 head `41772db92af3`。
  合第一個後，其餘必須改接，否則 `alembic upgrade head` 直接失敗。
- `tests/test_restaurant_api.py` 寫死表數；每個加表的 PR 都會在同幾行衝突，合併後數字要重算。
- 無 PR 的分支 `restaurant-system-competitor-analysis` 與 #29 建了同名資料表，兩者都套用會 `DuplicateTable`。

## 另有一項資安事項

有一項需要**輪替憑證**的事項已私下回報創辦人。細節刻意不寫進這個公開 repo。
