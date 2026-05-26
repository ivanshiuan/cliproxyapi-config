# Restaurant API — Phase 1 後端骨架

> 餐飲 SaaS 的 FastAPI 後端，Phase 1（極窄 MVP）的承載體。
> 目前為**骨架**：18 張核心表的 SQLAlchemy 2.x async ORM、Postgres+Redis 容器、FastAPI 入口、Alembic 配置。實際業務邏輯（POS 流程、損益計算、報廢扣帳）將由 DevSwarm 在後續任務中生成。

---

## 快速啟動

### 1. 開資料庫

```bash
make db-up           # 起 Postgres 16 + Redis 7 容器
```

### 2. 環境變數

```bash
cp restaurant_api/.env.example .env   # 預設值適用於本地 docker-compose
```

### 3. 跑遷移（首次建表）

```bash
cd restaurant_api
../.venv/bin/alembic revision --autogenerate -m "initial schema"
../.venv/bin/alembic upgrade head
```

> 第一次 revision 會掃描 `restaurant_api/models/` 並產出建表 DDL 到 `alembic/versions/`。
> 之後改 model 再跑一次 `revision --autogenerate` 就會 diff 出 schema 變更。

### 4. 啟動 API

```bash
make api             # uvicorn --reload，http://localhost:8000/docs
```

### 5. 健康檢查

```bash
curl http://localhost:8000/health
```

預期回應：
```json
{
  "status": "ok",
  "service": "restaurant_api",
  "version": "0.1.0",
  "checks": {
    "database": {"ok": true, "version": "PostgreSQL 16...", "database": "resto_dev"}
  }
}
```

---

## 目錄結構

```
restaurant_api/
├── __init__.py
├── main.py                  # FastAPI app + lifespan + /health /version /
├── config.py                # Pydantic Settings（RESTO_* 環境變數）
├── database.py              # async engine、session factory、健康探針
├── docker-compose.yml       # 開發用 Postgres 16 + Redis 7
├── alembic.ini              # Alembic 配置
├── alembic/
│   ├── env.py               # async-aware Alembic env
│   ├── script.py.mako       # 遷移範本（含 ruff format hook）
│   └── versions/            # 遷移檔（首次跑 autogenerate 才會有）
├── initdb/
│   └── 01_extensions.sql    # Postgres 擴充（uuid-ossp、citext、pg_trgm）
├── models/                  # 18 張核心表 SQLAlchemy 2.x ORM
│   ├── base.py              # Base、Money、Mixins、uuid7()
│   ├── tenants.py / stores.py / employees.py
│   ├── menu.py              # MenuCategory、MenuItem
│   ├── inventory.py         # Ingredient、Recipe、StockMovement（append-only）
│   ├── orders.py            # Order、OrderLine、OrderDiscount、OrderPayment
│   ├── cost_events.py       # WasteEvent、StaffMealEvent、TastingEvent
│   └── hr.py                # Shift、TimeClock、LeaveRequest
└── README.md                # 你正在看
```

## 模型重點

完整 DDL 與設計理由見 `docs/04_data_schema.md`。簡述：

| 設計 | 理由 |
|---|---|
| **UUIDv7 主鍵** | 時間排序友好的 PK，分散式佳，無中央自增瓶頸 |
| **Money = Numeric(14, 4)** | 4 位小數 + Decimal API，永不出現浮點誤差 |
| **`tenant_id` on every business table** | Phase 2 開啟 Postgres RLS 即可多租戶 |
| **append-only `stock_movements`** | 庫存事件溯源 ledger，從不 UPDATE/DELETE |
| **partial unique on current recipe** | BOM 時間版本化，但同時只有一個「現行版本」 |
| **`order_lines.cogs_actual` + `cogs_theoretical`** | 真實 vs 理論成本，餵養 `mv_daily_pnl` 物化視圖 |
| **HR `time_clocks` 含 4 段工時** | 勞基法 regular / OT 1.34x / OT 1.67x / holiday 2.0x |
| **`orders` 含 統一發票 / 載具 / 統編** | 串接電子發票 |

## 18 張表清單

```
tenants                      — 租戶（未來連鎖/加盟總部）
stores                       — 門市（含 Google Maps 預留）
employees                    — 員工

menu_categories              — 菜單分類
menu_items                   — 品項（含 POS 預留 external_pos_id）

ingredients                  — 食材
recipes                      — BOM 配方（時間版本化）
stock_movements              — 庫存事件 ledger（append-only）

orders                       — 訂單（含發票欄位）
order_lines                  — 訂單明細
order_discounts              — 折扣（含招待/折讓/員工折扣分流）
order_payments               — 收款（含平台費 fee_amount）

waste_events                 — 報廢
staff_meal_events            — 員工餐
tasting_events               — 試吃 / QC

shifts                       — 排班
time_clocks                  — 打卡（含 4 段工時）
leave_requests               — 請假（特休/事假/病假/...）
```

## Phase 1 接下來

DevSwarm 會依序生成（請見 `../specs/`）：

1. **`compute_daily_pnl`** — 真實損益純函式（demo 任務 `profit_calc.md`）
2. **BOM 扣料 service** — POS 賣出 → 自動寫 `stock_movements`
3. **訂單 router** — POST /orders、收款流程
4. **報廢/員工餐/試吃事件 router**
5. **打卡 router + 工時自動計算**
6. **報表 router** — 每日損益、損耗、人事工時

## 不會在 Phase 1 做

- 認證 / OAuth（Phase 2）
- 多租戶 RLS（Phase 2）
- CRM 會員體系（Phase 2）
- 行銷活動引擎（Phase 2）
- Google 地圖整合（Phase 3）
- 連鎖總部 / 加盟稽核（Phase 4）
- 三大自主運行閉環（Phase 5）

完整時程見 `../docs/03_roadmap.md`。
