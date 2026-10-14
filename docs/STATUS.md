# STATUS — 實測事實（機器產生，勿手改）

> 產生於 `2026-10-14 16:14 UTC` · commit `d283ba7` (2026-10-14T15:14:09+00:00) · 分支 `claude/business-process-uml-converter-1bd5bp`
> 重新產生：`make status-report`。這裡的每個數字都從 checkout 算出來（git / ORM / FastAPI app / migration 檔 / grep）。
> 其他文件若與這份衝突，**以這份為準**，並請把那份文件改成指向這裡，不要再手寫數字。

## 規模

| 項目 | 實測 |
|---|---|
| ORM 資料表 | **33** |
| API paths / operations / schemas | **59** / **69** / **95** |
| API 群組 | `/`, `admin`, `campaigns`, `clock`, `customers`, `events`, `health`, `kitchen`, `line`, `membership`, `orders`, `queue`, `reservations`, `stock`, `ugc`, `version` |
| Alembic migration 檔 | **10**；head：`41772db92af3` |
| 測試（餐飲 + DevSwarm，`make full-check`） | **459** 個 |
| 測試（TIGER LINE，`make tigerline-check`） | **181** 個 |

## 程式碼量（git 追蹤檔）

| 目錄 | 檔案數 | Python 行數 |
|---|---:|---:|
| `restaurant_api/` | 115 | 19,258 |
| `devswarm/` | 29 | 4,005 |
| `tigerline/` | 49 | 4,238 |
| `tests/` | 78 | 13,776 |
| `scripts/` | 16 | 2,310 |
| `sports-intelligence-desk/` | 28 | 0 |
| `tigerline-web/` | 18 | 0 |
| 全部 | 402 | 43,587 |

## 計算引擎有沒有真的接到系統（grep 應用程式碼，排除 `services/calc/` 與測試）

| 引擎函式 | 用途 | 應用程式呼叫點 | 狀態 |
|---|---|---:|---|
| `consume_bom` | BOM 扣料 | 8 | ✅ 已接線 |
| `resolve_discounts` | 折扣疊加 | 4 | ✅ 已接線 |
| `detect_variance` | COGS 異常偵測 | 2 | ✅ 已接線 |
| `classify_hours` | 勞基法工時分桶 | 7 | ✅ 已接線 |
| `validate_uniform_invoice` | 統一編號檢核 | 0 | ❌ **沒有任何呼叫者（孤兒程式碼）** |
| `compute_daily_pnl` | 每日真實損益 | 0 | ❌ **沒有任何呼叫者（孤兒程式碼）** |

## 工具版本（本次產生時）

| 工具 | 版本 |
|---|---|
| ruff | 0.17.0 |
| pyright | 1.1.414 |
| SQLAlchemy | 2.1.4 |
| FastAPI | 0.143.0 |
| pydantic | 2.14.1 |
| alembic | 1.20.0 |
| Python | 3.12.3 |

> ruff / pyright 版本鎖在 `requirements-dev.txt`；執行期依賴只鎖大版號，所以 SQLAlchemy 等會隨時間浮動。

_檔案頂層目錄分佈：restaurant_api 115, tests 78, tigerline 49, devswarm 29, sports-intelligence-desk 28, docs 24, tigerline-web 18, scripts 16_
