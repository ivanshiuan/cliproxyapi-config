# 04 — Core Data Schema (Narrow MVP)

**Status:** Canonical DDL for the first-store launch. PostgreSQL 16.
**Scope:** Real P&L · ERP 進銷存 · BOM 自動扣料 · 隱性成本 (招待/折扣/折讓/報廢/員工餐/試吃) · 基礎人事 (打卡/排班/請假).
**Out of scope (later phases):** CRM, 行銷自動化, Google Maps 行為, 多店分析, K8s, FastAPI models.

This document is the contract. Every backend service reads/writes through these tables. If a feature can't be expressed here, the feature is wrong — or the schema is, and we change the schema by ADR.

---

## 0. Architectural decisions (made, not surveyed)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Primary keys | **UUIDv7** (`uuid` type, generated app-side or via `uuid_generate_v7()` extension) | Time-ordered like BIGSERIAL → keeps B-tree locality and chronological sort. Globally unique → safe for offline POS sync, mobile-first ingestion, future multi-region. We pay 8 bytes vs BIGSERIAL; worth it. |
| 2 | Multi-tenancy | **Single DB, `tenant_id` column on every business table + RLS policy** | One store today, chains/加盟 tomorrow. DB-per-tenant kills cross-tenant analytics and 10× ops cost. RLS is the safety net against query bugs. |
| 3 | Stock movements | **Append-only event ledger** (`stock_movements`) + periodic `inventory_snapshots` | Restaurant inventory is the #1 source of fraud and shrinkage disputes. We MUST be able to replay history. Snapshots are a perf optimization, not source of truth. |
| 4 | Money precision | **`numeric(14,4)`** | TWD has no decimals in retail, but BOM unit cost (e.g. 0.0125 TWD per gram of salt) needs 4 dp. 14 digits → max 9,999,999,999.9999 — enough for a chain's annual revenue in TWD. |
| 5 | Soft delete | **`deleted_at timestamptz NULL`** on user-facing records (menu items, suppliers, employees). **Hard-immutable** for financial events (orders, stock_movements, payroll) — those get reversal entries instead. | Restaurants want "oops un-delete" for menu mistakes. They legally CANNOT delete invoices or payroll. Two different patterns, on purpose. |

**Convention applied to every business table:**

- `id uuid PRIMARY KEY DEFAULT uuid_generate_v7()`
- `tenant_id uuid NOT NULL` — partition key, RLS enforced
- `store_id uuid NOT NULL` — even for HQ-level tables, default to the "primary" store for MVP
- `created_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()` — kept current by trigger `trg_touch_updated_at`
- `deleted_at timestamptz NULL` where applicable
- All timestamps stored UTC; render via `AT TIME ZONE 'Asia/Taipei'` in views.
- Currency defaults to `'TWD'` but column exists for forward compat.

```sql
-- One-time setup
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
-- uuid_generate_v7() — install via pg_uuidv7 extension or implement as PL/pgSQL until PG18

CREATE OR REPLACE FUNCTION trg_touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
```

---

## 1. Tenancy & Store core

### ERD

| Table | Purpose |
|---|---|
| `tenants` | 公司 / 法人實體 (one per 統編) |
| `stores` | 門市 (one row in MVP; designed for chains) |
| `employees` | 員工主檔 (also used by 人事 module) |
| `users` | 系統登入帳號 (1:1 or 1:N with employees) |

### DDL

```sql
CREATE TABLE tenants (
  id           uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  legal_name   text NOT NULL,                       -- 法人名稱
  tax_id       varchar(8) NOT NULL UNIQUE,          -- 統一編號 (8 digits)
  currency_code char(3) NOT NULL DEFAULT 'TWD',
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz
);

CREATE TABLE stores (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  code            text NOT NULL,                    -- 內部代碼 e.g. 'TPE-01'
  display_name    text NOT NULL,
  address         text,
  phone           text,
  -- Google Maps forward compat
  google_place_id text,
  latitude        numeric(9,6),
  longitude       numeric(9,6),
  -- Operational
  open_time       time,
  close_time      time,
  timezone        text NOT NULL DEFAULT 'Asia/Taipei',
  opened_on       date,                             -- 開幕日 — drives 折舊攤提起算
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz,
  UNIQUE (tenant_id, code)
);

CREATE TABLE employees (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  store_id      uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  employee_code text NOT NULL,                      -- 員工編號
  full_name     text NOT NULL,
  national_id   text,                               -- 身分證 (encrypted at app layer)
  role          text NOT NULL,                      -- 'manager'|'chef'|'server'|'parttime'
  employment_type text NOT NULL,                    -- 'full_time'|'part_time'|'hourly'
  hourly_rate   numeric(14,4),                      -- NULL for monthly
  monthly_salary numeric(14,4),
  hired_on      date NOT NULL,
  terminated_on date,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz,
  UNIQUE (tenant_id, employee_code)
);

CREATE TABLE users (
  id           uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  store_id     uuid REFERENCES stores(id) ON DELETE SET NULL,
  employee_id  uuid REFERENCES employees(id) ON DELETE SET NULL,
  email        text NOT NULL,
  password_hash text NOT NULL,
  role_scope   text NOT NULL,                       -- 'tenant_admin'|'store_manager'|'staff'
  last_login_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  UNIQUE (tenant_id, email)
);

CREATE INDEX idx_stores_tenant       ON stores(tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_employees_store     ON employees(store_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_tenant_email  ON users(tenant_id, email) WHERE deleted_at IS NULL;
```

---

## 2. Menu & BOM (配方)

### ERD

| Table | Purpose |
|---|---|
| `menu_categories` | 菜單分類 (前菜/主餐/飲料) |
| `menu_items` | 販售品項 |
| `ingredients` | 食材原物料主檔 |
| `units_of_measure` | 計量單位 (g, ml, pcs) — reference data |
| `recipes` | menu_item ↔ ingredient 配方 (BOM) |

### DDL

```sql
CREATE TABLE units_of_measure (
  code        text PRIMARY KEY,                    -- 'g','kg','ml','L','pcs'
  display_name text NOT NULL,
  base_unit   text,                                -- canonical: 'g' for mass, 'ml' for volume, 'pcs' for count
  factor_to_base numeric(14,6) NOT NULL DEFAULT 1  -- e.g. kg → 1000 (g)
);

CREATE TABLE menu_categories (
  id           uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  store_id     uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  name         text NOT NULL,
  sort_order   int NOT NULL DEFAULT 0,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz
);

CREATE TABLE menu_items (
  id               uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  store_id         uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  category_id      uuid REFERENCES menu_categories(id) ON DELETE SET NULL,
  sku              text NOT NULL,                  -- 內部商品碼
  name             text NOT NULL,
  price            numeric(14,4) NOT NULL,         -- 售價 (含稅)
  currency_code    char(3) NOT NULL DEFAULT 'TWD',
  is_active        boolean NOT NULL DEFAULT true,
  is_taxable       boolean NOT NULL DEFAULT true,
  -- POS 整合預留
  external_pos_id  text,
  pos_source       text,                           -- 'ichef'|'posplus'|'manual'
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz,
  UNIQUE (tenant_id, store_id, sku)
);

CREATE TABLE ingredients (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  store_id          uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  sku               text NOT NULL,                 -- 食材編碼
  name              text NOT NULL,                 -- e.g. '雞胸肉'
  unit_code         text NOT NULL REFERENCES units_of_measure(code),
  -- Standard cost — used for theoretical COGS. Actual cost comes from purchase_order_lines (weighted avg).
  standard_unit_cost numeric(14,4) NOT NULL DEFAULT 0,
  reorder_point     numeric(14,4),                 -- 安全庫存
  shelf_life_days   int,                           -- 保存天數 (for 報廢 alerts)
  is_active         boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  UNIQUE (tenant_id, store_id, sku)
);

CREATE TABLE recipes (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id       uuid NOT NULL,
  store_id        uuid NOT NULL,
  menu_item_id    uuid NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
  ingredient_id   uuid NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
  quantity        numeric(14,4) NOT NULL,          -- 每份用量, in ingredient's unit
  yield_factor    numeric(6,4) NOT NULL DEFAULT 1.0, -- 損耗率倒數: 0.9 = 10% prep loss
  effective_from  date NOT NULL DEFAULT CURRENT_DATE,
  effective_to    date,                            -- NULL = current
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (menu_item_id, ingredient_id, effective_from)
);

CREATE INDEX idx_menu_items_active  ON menu_items(tenant_id, store_id) WHERE is_active AND deleted_at IS NULL;
CREATE INDEX idx_recipes_menu_item  ON recipes(menu_item_id) WHERE effective_to IS NULL;
CREATE INDEX idx_ingredients_active ON ingredients(tenant_id, store_id) WHERE is_active AND deleted_at IS NULL;
```

**Invariant:** for any given `(menu_item_id, ingredient_id)`, at most one row has `effective_to IS NULL`. Enforced by partial unique index:

```sql
CREATE UNIQUE INDEX uq_recipes_current
  ON recipes(menu_item_id, ingredient_id)
  WHERE effective_to IS NULL;
```

---

## 3. ERP — Suppliers, Purchasing, Inventory

### ERD

| Table | Purpose |
|---|---|
| `suppliers` | 供應商主檔 |
| `purchase_orders` | 進貨單 header |
| `purchase_order_lines` | 進貨單明細 (drives weighted-avg cost) |
| `stock_movements` | **Append-only** ledger of all stock changes |
| `inventory_snapshots` | 每日結存快照 (perf cache, not source of truth) |

### DDL

```sql
CREATE TABLE suppliers (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  store_id      uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  code          text NOT NULL,
  name          text NOT NULL,
  tax_id        varchar(8),                        -- 統編 (8 digits)
  contact_name  text,
  phone         text,
  email         text,
  payment_terms text,                              -- '月結30'|'貨到付款'
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz,
  UNIQUE (tenant_id, code)
);

CREATE TABLE purchase_orders (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id       uuid NOT NULL,
  store_id        uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  supplier_id     uuid NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
  po_number       text NOT NULL,                   -- 進貨單號
  ordered_at      timestamptz NOT NULL,
  received_at     timestamptz,                     -- NULL = pending
  status          text NOT NULL DEFAULT 'draft',   -- 'draft'|'ordered'|'received'|'cancelled'
  subtotal        numeric(14,4) NOT NULL DEFAULT 0,
  tax_amount      numeric(14,4) NOT NULL DEFAULT 0,
  total           numeric(14,4) NOT NULL DEFAULT 0,
  currency_code   char(3) NOT NULL DEFAULT 'TWD',
  invoice_number  text,                            -- 供應商開立之統一發票號
  notes           text,
  created_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, po_number)
);

CREATE TABLE purchase_order_lines (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id         uuid NOT NULL,
  store_id          uuid NOT NULL,
  purchase_order_id uuid NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
  ingredient_id     uuid NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
  quantity          numeric(14,4) NOT NULL,
  unit_code         text NOT NULL REFERENCES units_of_measure(code),
  unit_price        numeric(14,4) NOT NULL,         -- 單價 (excl tax)
  line_total        numeric(14,4) NOT NULL,
  expires_on        date,                           -- 效期 — drives FEFO + 報廢 alerts
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- THE LEDGER. Immutable. No UPDATE, no DELETE.
CREATE TABLE stock_movements (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id       uuid NOT NULL,
  store_id        uuid NOT NULL,
  ingredient_id   uuid NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  movement_type   text NOT NULL,                    -- see enum below
  quantity        numeric(14,4) NOT NULL,           -- signed: +inbound / -outbound
  unit_cost       numeric(14,4) NOT NULL,           -- weighted avg cost AT TIME OF MOVEMENT
  -- Source linkage (sparse — exactly one populated based on movement_type)
  source_po_line_id    uuid REFERENCES purchase_order_lines(id) ON DELETE RESTRICT,
  source_order_line_id uuid,                        -- FK added after orders table defined
  source_waste_id      uuid,                        -- FK to waste_events
  source_adjustment_id uuid,                        -- FK to stock_adjustments
  notes           text,
  created_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (movement_type IN (
    'purchase_receipt',    -- 進貨
    'sale_consumption',    -- 銷售扣料 (via BOM)
    'waste',               -- 報廢
    'staff_meal',          -- 員工餐
    'tasting',             -- 試吃
    'comp',                -- 招待
    'adjustment_in',       -- 盤點調整 +
    'adjustment_out',      -- 盤點調整 -
    'transfer_in',         -- 跨店調撥 (future)
    'transfer_out'
  )),
  CHECK (
    (movement_type IN ('purchase_receipt','adjustment_in','transfer_in')  AND quantity > 0) OR
    (movement_type IN ('sale_consumption','waste','staff_meal','tasting','comp','adjustment_out','transfer_out') AND quantity < 0)
  )
);

-- Block UPDATE/DELETE on the ledger
CREATE RULE stock_movements_no_update AS ON UPDATE TO stock_movements DO INSTEAD NOTHING;
CREATE RULE stock_movements_no_delete AS ON DELETE TO stock_movements DO INSTEAD NOTHING;

CREATE TABLE inventory_snapshots (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id       uuid NOT NULL,
  store_id        uuid NOT NULL,
  ingredient_id   uuid NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
  snapshot_date   date NOT NULL,                    -- 結算日 (TPE)
  quantity        numeric(14,4) NOT NULL,           -- 結存量
  avg_unit_cost   numeric(14,4) NOT NULL,           -- 結存平均成本
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (store_id, ingredient_id, snapshot_date)
);

CREATE TABLE stock_adjustments (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  ingredient_id uuid NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
  adjusted_at   timestamptz NOT NULL DEFAULT now(),
  expected_qty  numeric(14,4) NOT NULL,             -- 系統量
  counted_qty   numeric(14,4) NOT NULL,             -- 實盤量
  variance      numeric(14,4) GENERATED ALWAYS AS (counted_qty - expected_qty) STORED,
  reason        text NOT NULL,                       -- '盤點'|'估算修正'|'其他'
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_stock_mov_ing_time   ON stock_movements(ingredient_id, occurred_at DESC);
CREATE INDEX idx_stock_mov_store_day  ON stock_movements(store_id, (occurred_at::date));
CREATE INDEX idx_stock_mov_type       ON stock_movements(store_id, movement_type, occurred_at);
CREATE INDEX idx_po_lines_po          ON purchase_order_lines(purchase_order_id);
CREATE INDEX idx_inv_snap_lookup      ON inventory_snapshots(store_id, snapshot_date DESC, ingredient_id);
```

**Invariants:**

1. `inventory_snapshots.quantity` on date D == `SUM(stock_movements.quantity)` for that ingredient where `occurred_at <= end_of_day(D, TPE)`. Reconciled nightly.
2. Every `stock_movements` row has exactly one non-NULL `source_*_id` matching its `movement_type`.
3. `stock_movements` is **append-only** — rules above enforce.
4. Negative on-hand is allowed (record of truth) but raises an `异常成本` flag in the P&L view.

---

## 4. Orders, Discounts, Comps, Waste, Staff Meals, Tastings

The "hidden cost" core. Every leak is a typed row.

### ERD

| Table | Purpose |
|---|---|
| `orders` | 銷售訂單 header (POS-agnostic) |
| `order_lines` | 訂單明細 — drives BOM auto-deduct |
| `order_discounts` | 折扣/招待 line-level or order-level |
| `order_payments` | 收款明細 (cash/card/LinePay/街口) |
| `waste_events` | 報廢事件 |
| `staff_meal_events` | 員工餐 |
| `tasting_events` | 試吃 / 試菜 |

### DDL

```sql
CREATE TABLE orders (
  id                 uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id          uuid NOT NULL,
  store_id           uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  order_number       text NOT NULL,                 -- 內部單號
  opened_at          timestamptz NOT NULL DEFAULT now(),
  closed_at          timestamptz,
  status             text NOT NULL DEFAULT 'open',  -- 'open'|'closed'|'voided'
  channel            text NOT NULL DEFAULT 'dine_in', -- 'dine_in'|'takeout'|'delivery'|'online'
  table_number       text,
  guest_count        int,
  -- 金額
  subtotal           numeric(14,4) NOT NULL DEFAULT 0, -- 折前小計
  discount_total     numeric(14,4) NOT NULL DEFAULT 0,
  service_charge     numeric(14,4) NOT NULL DEFAULT 0, -- 服務費
  tax_amount         numeric(14,4) NOT NULL DEFAULT 0,
  total              numeric(14,4) NOT NULL DEFAULT 0, -- 應收
  currency_code      char(3) NOT NULL DEFAULT 'TWD',
  -- 統一發票 (Taiwan-specific)
  invoice_number     varchar(10),                    -- 統一發票號碼 e.g. AB12345678
  invoice_issued_at  timestamptz,
  carrier_type       text,                           -- 載具類型: 'mobile'|'citizen'|'member'|'paper'
  carrier_id         text,                           -- 載具號碼
  buyer_tax_id       varchar(8),                     -- 買方統編 (B2B)
  -- POS 整合預留
  external_pos_id    text,
  pos_source         text,                           -- 'ichef'|'posplus'|'manual'
  -- 異常標記
  voided_at          timestamptz,
  voided_reason      text,
  created_by         uuid REFERENCES users(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, store_id, order_number)
);

CREATE TABLE order_lines (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id       uuid NOT NULL,
  store_id        uuid NOT NULL,
  order_id        uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  menu_item_id    uuid NOT NULL REFERENCES menu_items(id) ON DELETE RESTRICT,
  quantity        numeric(14,4) NOT NULL,
  unit_price      numeric(14,4) NOT NULL,           -- snapshot of menu_items.price at sale
  line_subtotal   numeric(14,4) NOT NULL,
  line_discount   numeric(14,4) NOT NULL DEFAULT 0,
  line_total      numeric(14,4) NOT NULL,
  -- 異常標記
  is_comp         boolean NOT NULL DEFAULT false,    -- 整行招待
  comp_reason     text,
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Now we can wire the FK from stock_movements
ALTER TABLE stock_movements
  ADD CONSTRAINT fk_stock_mov_order_line
  FOREIGN KEY (source_order_line_id) REFERENCES order_lines(id) ON DELETE RESTRICT;

CREATE TABLE order_discounts (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  order_id      uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  order_line_id uuid REFERENCES order_lines(id) ON DELETE CASCADE, -- NULL = order-level
  discount_type text NOT NULL,                       -- 'pct'|'amount'|'comp'|'allowance'|'employee'
  -- 'comp' = 招待, 'allowance' = 折讓, 'employee' = 員工優惠
  value         numeric(14,4) NOT NULL,              -- pct: 0.10 = 10%; amount: TWD
  amount_applied numeric(14,4) NOT NULL,             -- realised TWD effect
  reason        text,                                -- audit
  approved_by   uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE order_payments (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  order_id      uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  paid_at       timestamptz NOT NULL DEFAULT now(),
  method        text NOT NULL,                       -- 'cash'|'credit_card'|'line_pay'|'jko_pay'|'uber_eats'|'foodpanda'
  amount        numeric(14,4) NOT NULL,
  fee_amount    numeric(14,4) NOT NULL DEFAULT 0,    -- 平台抽成 / 刷卡手續費 — KEY for real P&L
  external_ref  text,                                -- 平台訂單號
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 報廢
CREATE TABLE waste_events (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  ingredient_id uuid REFERENCES ingredients(id) ON DELETE RESTRICT,
  menu_item_id  uuid REFERENCES menu_items(id) ON DELETE RESTRICT,
  quantity      numeric(14,4) NOT NULL,              -- in ingredient unit OR menu_item portions
  reason        text NOT NULL,                        -- 'expired'|'spoiled'|'cooking_error'|'dropped'|'other'
  cost_amount   numeric(14,4) NOT NULL,              -- TWD impact
  reported_by   uuid REFERENCES employees(id),
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (ingredient_id IS NOT NULL OR menu_item_id IS NOT NULL)
);

-- 員工餐
CREATE TABLE staff_meal_events (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  employee_id   uuid NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
  menu_item_id  uuid REFERENCES menu_items(id) ON DELETE RESTRICT,
  ingredient_id uuid REFERENCES ingredients(id) ON DELETE RESTRICT,
  quantity      numeric(14,4) NOT NULL DEFAULT 1,
  cost_amount   numeric(14,4) NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 試吃 / 試菜 (R&D, VIP tasting, media)
CREATE TABLE tasting_events (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  purpose       text NOT NULL,                        -- 'rnd'|'vip'|'media'|'training'
  menu_item_id  uuid REFERENCES menu_items(id) ON DELETE RESTRICT,
  ingredient_id uuid REFERENCES ingredients(id) ON DELETE RESTRICT,
  quantity      numeric(14,4) NOT NULL,
  cost_amount   numeric(14,4) NOT NULL,
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE stock_movements
  ADD CONSTRAINT fk_stock_mov_waste
  FOREIGN KEY (source_waste_id) REFERENCES waste_events(id) ON DELETE RESTRICT;

ALTER TABLE stock_movements
  ADD CONSTRAINT fk_stock_mov_adj
  FOREIGN KEY (source_adjustment_id) REFERENCES stock_adjustments(id) ON DELETE RESTRICT;

CREATE INDEX idx_orders_store_day        ON orders(store_id, (opened_at::date)) WHERE status <> 'voided';
CREATE INDEX idx_orders_closed_at        ON orders(store_id, closed_at) WHERE status = 'closed';
CREATE INDEX idx_order_lines_order       ON order_lines(order_id);
CREATE INDEX idx_order_lines_menu_item   ON order_lines(menu_item_id);
CREATE INDEX idx_order_payments_method   ON order_payments(store_id, method, paid_at);
CREATE INDEX idx_waste_store_day         ON waste_events(store_id, (occurred_at::date));
CREATE INDEX idx_staff_meal_store_day    ON staff_meal_events(store_id, (occurred_at::date));
CREATE INDEX idx_tasting_store_day       ON tasting_events(store_id, (occurred_at::date));
CREATE INDEX idx_orders_invoice          ON orders(invoice_number) WHERE invoice_number IS NOT NULL;
```

**Invariants:**

1. `orders.total = subtotal - discount_total + service_charge + tax_amount` (enforced by application; nightly audit job flags drift).
2. `SUM(order_payments.amount) = orders.total` for closed, non-voided orders.
3. Every closed `order_line` produces N `stock_movements` of type `sale_consumption`, one per ingredient in the recipe active at `orders.opened_at`.
4. `waste_events`, `staff_meal_events`, `tasting_events` each produce one `stock_movements` row (typed accordingly) and one cost line in the P&L view.

---

## 5. 人事 — HR (打卡 / 排班 / 請假)

### ERD

| Table | Purpose |
|---|---|
| `shifts` | 排班 (planned) |
| `time_clocks` | 打卡 (actual punch in/out) |
| `leave_requests` | 請假 / 換班 |
| `payroll_periods` | 薪資週期 (drives 人事成本攤提 in P&L) |

### DDL

```sql
CREATE TABLE shifts (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  employee_id   uuid NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
  shift_date    date NOT NULL,
  start_at      timestamptz NOT NULL,
  end_at        timestamptz NOT NULL,
  role_label    text,                                -- '外場'|'內場'|'吧檯'
  status        text NOT NULL DEFAULT 'scheduled',   -- 'scheduled'|'swapped'|'cancelled'
  swap_with_id  uuid REFERENCES shifts(id),           -- 換班對象
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (end_at > start_at)
);

CREATE TABLE time_clocks (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  employee_id   uuid NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
  shift_id      uuid REFERENCES shifts(id) ON DELETE SET NULL,
  clock_in_at   timestamptz NOT NULL,
  clock_out_at  timestamptz,
  -- 勞基法 categorisation
  hours_regular   numeric(6,2),                       -- 正常工時
  hours_overtime_1 numeric(6,2),                      -- 加班 1.34x (前2小時)
  hours_overtime_2 numeric(6,2),                      -- 加班 1.67x (後2小時)
  hours_holiday   numeric(6,2),                       -- 假日加班
  source        text NOT NULL DEFAULT 'manual',       -- 'manual'|'qr'|'face'|'gps'
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE leave_requests (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  employee_id   uuid NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
  leave_type    text NOT NULL,                        -- '特休'|'事假'|'病假'|'婚假'|'喪假'|'產假'|'生理假'|'公假'
  start_at      timestamptz NOT NULL,
  end_at        timestamptz NOT NULL,
  hours         numeric(6,2) NOT NULL,
  reason        text,
  status        text NOT NULL DEFAULT 'pending',      -- 'pending'|'approved'|'rejected'|'cancelled'
  is_paid       boolean NOT NULL DEFAULT false,
  approved_by   uuid REFERENCES users(id),
  approved_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (end_at > start_at)
);

CREATE TABLE payroll_periods (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL,
  period_start  date NOT NULL,
  period_end    date NOT NULL,
  total_labor_cost numeric(14,4) NOT NULL DEFAULT 0,  -- 含勞健保
  status        text NOT NULL DEFAULT 'open',         -- 'open'|'closed'|'paid'
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (store_id, period_start, period_end)
);

CREATE INDEX idx_shifts_emp_date    ON shifts(employee_id, shift_date);
CREATE INDEX idx_shifts_store_date  ON shifts(store_id, shift_date);
CREATE INDEX idx_time_clocks_emp_in ON time_clocks(employee_id, clock_in_at);
CREATE INDEX idx_time_clocks_store_day ON time_clocks(store_id, (clock_in_at::date));
CREATE INDEX idx_leave_emp_period   ON leave_requests(employee_id, start_at);
```

---

## 6. 固定成本 — Fixed cost amortization

Daily P&L needs rent/utilities/depreciation prorated.

```sql
CREATE TABLE fixed_cost_items (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v7(),
  tenant_id     uuid NOT NULL,
  store_id      uuid NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  category      text NOT NULL,                        -- 'rent'|'utility_baseline'|'depreciation'|'insurance'|'software'|'other'
  description   text NOT NULL,
  amount        numeric(14,4) NOT NULL,               -- 期間總額
  period_start  date NOT NULL,
  period_end    date NOT NULL,                         -- amortize daily over [start, end]
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz,
  CHECK (period_end >= period_start)
);

CREATE INDEX idx_fixed_cost_store_period
  ON fixed_cost_items(store_id, period_start, period_end)
  WHERE deleted_at IS NULL;
```

---

## 7. 真實損益 — The daily P&L materialized view

This is the whole point of the product. Every cost leak is a column.

```sql
-- Helper: daily prorated fixed cost
CREATE OR REPLACE VIEW v_daily_fixed_cost AS
SELECT
  f.tenant_id,
  f.store_id,
  d::date AS business_date,
  SUM(f.amount / GREATEST((f.period_end - f.period_start + 1), 1)) AS fixed_cost_daily
FROM fixed_cost_items f
CROSS JOIN LATERAL generate_series(f.period_start, f.period_end, '1 day'::interval) AS d
WHERE f.deleted_at IS NULL
GROUP BY f.tenant_id, f.store_id, d::date;

-- Theoretical COGS = sum of (recipe qty * standard cost) per closed order line.
-- Actual COGS = sum of stock_movements where movement_type='sale_consumption' (unit_cost * |quantity|).
-- The delta is the 異常成本 signal.

CREATE MATERIALIZED VIEW mv_daily_pnl AS
WITH
  bizday AS (
    SELECT
      o.tenant_id,
      o.store_id,
      (o.closed_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
      o.id AS order_id,
      o.subtotal,
      o.discount_total,
      o.total
    FROM orders o
    WHERE o.status = 'closed'
  ),
  revenue AS (
    SELECT tenant_id, store_id, business_date,
           SUM(subtotal)       AS gross_revenue,
           SUM(discount_total) AS discount_total,
           SUM(total)          AS net_revenue
    FROM bizday
    GROUP BY 1,2,3
  ),
  -- Actual COGS from the ledger (truth)
  cogs_actual AS (
    SELECT sm.tenant_id, sm.store_id,
           (sm.occurred_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
           SUM(ABS(sm.quantity) * sm.unit_cost) AS cogs_actual
    FROM stock_movements sm
    WHERE sm.movement_type = 'sale_consumption'
    GROUP BY 1,2,3
  ),
  -- Theoretical COGS from recipe × order_lines × ingredient.standard_unit_cost
  cogs_theoretical AS (
    SELECT ol.tenant_id, ol.store_id,
           (o.closed_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
           SUM(ol.quantity * r.quantity / r.yield_factor * i.standard_unit_cost) AS cogs_theoretical
    FROM order_lines ol
    JOIN orders o      ON o.id = ol.order_id AND o.status = 'closed'
    JOIN recipes r     ON r.menu_item_id = ol.menu_item_id
                      AND r.effective_from <= o.opened_at::date
                      AND (r.effective_to IS NULL OR r.effective_to > o.opened_at::date)
    JOIN ingredients i ON i.id = r.ingredient_id
    GROUP BY 1,2,3
  ),
  -- Hidden cost leaks — each itemized
  waste_cost AS (
    SELECT tenant_id, store_id,
           (occurred_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
           SUM(cost_amount) AS waste_cost
    FROM waste_events GROUP BY 1,2,3
  ),
  staff_meal_cost AS (
    SELECT tenant_id, store_id,
           (occurred_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
           SUM(cost_amount) AS staff_meal_cost
    FROM staff_meal_events GROUP BY 1,2,3
  ),
  tasting_cost AS (
    SELECT tenant_id, store_id,
           (occurred_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
           SUM(cost_amount) AS tasting_cost
    FROM tasting_events GROUP BY 1,2,3
  ),
  comp_cost AS (
    -- 招待 cost is the COGS of comped lines (revenue impact already in discount_total)
    SELECT ol.tenant_id, ol.store_id,
           (o.closed_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
           SUM(ol.quantity * r.quantity / r.yield_factor * i.standard_unit_cost) AS comp_cost
    FROM order_lines ol
    JOIN orders o      ON o.id = ol.order_id AND o.status='closed'
    JOIN recipes r     ON r.menu_item_id = ol.menu_item_id AND r.effective_to IS NULL
    JOIN ingredients i ON i.id = r.ingredient_id
    WHERE ol.is_comp
    GROUP BY 1,2,3
  ),
  -- Variable costs: platform fees + card fees (from order_payments.fee_amount)
  platform_fees AS (
    SELECT op.tenant_id, op.store_id,
           (op.paid_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
           SUM(op.fee_amount) AS platform_fees
    FROM order_payments op GROUP BY 1,2,3
  ),
  -- Labor cost — prorated from time_clocks * hourly_rate (or daily share of monthly salary)
  labor_cost AS (
    SELECT tc.tenant_id, tc.store_id,
           (tc.clock_in_at AT TIME ZONE 'Asia/Taipei')::date AS business_date,
           SUM(
             COALESCE(e.hourly_rate, e.monthly_salary/30/8, 0)
             * ( COALESCE(tc.hours_regular,0)
               + COALESCE(tc.hours_overtime_1,0)*1.34
               + COALESCE(tc.hours_overtime_2,0)*1.67
               + COALESCE(tc.hours_holiday,0)*2.0 )
           ) AS labor_cost
    FROM time_clocks tc
    JOIN employees e ON e.id = tc.employee_id
    WHERE tc.clock_out_at IS NOT NULL
    GROUP BY 1,2,3
  )
SELECT
  r.tenant_id,
  r.store_id,
  r.business_date,
  -- 營收
  r.gross_revenue,
  r.discount_total,
  r.net_revenue,
  -- 成本明細
  COALESCE(ca.cogs_actual, 0)        AS cogs_actual,
  COALESCE(ct.cogs_theoretical, 0)   AS cogs_theoretical,
  COALESCE(w.waste_cost, 0)          AS waste_cost,
  COALESCE(sm.staff_meal_cost, 0)    AS staff_meal_cost,
  COALESCE(t.tasting_cost, 0)        AS tasting_cost,
  COALESCE(c.comp_cost, 0)           AS comp_cost,
  COALESCE(pf.platform_fees, 0)      AS platform_fees,
  COALESCE(lc.labor_cost, 0)         AS labor_cost,
  COALESCE(fc.fixed_cost_daily, 0)   AS fixed_cost_daily,
  -- 真實毛利 = 淨營收 − 實際食材成本
  r.net_revenue - COALESCE(ca.cogs_actual,0) AS gross_profit_real,
  -- 真實淨利 = 毛利 − 變動費用 − 人事 − 固定攤提
  r.net_revenue
    - COALESCE(ca.cogs_actual,0)
    - COALESCE(pf.platform_fees,0)
    - COALESCE(lc.labor_cost,0)
    - COALESCE(fc.fixed_cost_daily,0)
    AS net_profit_real,
  -- 異常成本訊號: 實際 vs 理論差異 > 5% of 淨營收
  CASE
    WHEN r.net_revenue > 0
      AND ABS(COALESCE(ca.cogs_actual,0) - COALESCE(ct.cogs_theoretical,0))
          / NULLIF(r.net_revenue, 0) > 0.05
    THEN true ELSE false
  END AS cogs_variance_flag,
  COALESCE(ca.cogs_actual,0) - COALESCE(ct.cogs_theoretical,0) AS cogs_variance_amount,
  now() AS refreshed_at
FROM revenue r
LEFT JOIN cogs_actual      ca ON (ca.store_id, ca.business_date) = (r.store_id, r.business_date)
LEFT JOIN cogs_theoretical ct ON (ct.store_id, ct.business_date) = (r.store_id, r.business_date)
LEFT JOIN waste_cost       w  ON (w.store_id,  w.business_date)  = (r.store_id, r.business_date)
LEFT JOIN staff_meal_cost  sm ON (sm.store_id, sm.business_date) = (r.store_id, r.business_date)
LEFT JOIN tasting_cost     t  ON (t.store_id,  t.business_date)  = (r.store_id, r.business_date)
LEFT JOIN comp_cost        c  ON (c.store_id,  c.business_date)  = (r.store_id, r.business_date)
LEFT JOIN platform_fees    pf ON (pf.store_id, pf.business_date) = (r.store_id, r.business_date)
LEFT JOIN labor_cost       lc ON (lc.store_id, lc.business_date) = (r.store_id, r.business_date)
LEFT JOIN v_daily_fixed_cost fc ON (fc.store_id, fc.business_date) = (r.store_id, r.business_date);

CREATE UNIQUE INDEX uq_mv_daily_pnl ON mv_daily_pnl(store_id, business_date);
CREATE INDEX idx_mv_daily_pnl_flag  ON mv_daily_pnl(store_id, business_date) WHERE cogs_variance_flag;

-- Refresh nightly + on-demand from API
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_pnl;
```

---

## 8. Row-Level Security (tenant isolation)

```sql
-- Enable on every business table. Example for orders:
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_orders ON orders
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
-- Application sets: SET LOCAL app.tenant_id = '...'; at session start.
```

Apply the same pattern to: `stores, employees, users, menu_categories, menu_items, ingredients, recipes, suppliers, purchase_orders, purchase_order_lines, stock_movements, inventory_snapshots, stock_adjustments, order_lines, order_discounts, order_payments, waste_events, staff_meal_events, tasting_events, shifts, time_clocks, leave_requests, payroll_periods, fixed_cost_items`.

---

## 9. Triggers (the boring necessary stuff)

```sql
-- Apply trg_touch_updated_at to every table with updated_at.
-- Example — repeat for all:
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON orders
  FOR EACH ROW EXECUTE FUNCTION trg_touch_updated_at();
```

A migration helper should generate these in a loop over `information_schema`.

---

## 10. Implementation order (one sprint)

1. Day 1-2: Sections 1, 2 (tenant, store, menu, BOM) — seed data, RLS scaffolding.
2. Day 3-4: Section 3 (ERP ledger) — write a stress test that replays 10k movements and reconciles to snapshots.
3. Day 5-6: Section 4 (orders + leaks) — write the BOM auto-deduct trigger/service.
4. Day 7: Section 5 (HR) — basic CRUD.
5. Day 8: Section 6 + 7 (fixed cost + mv_daily_pnl) — wire the dashboard.
6. Day 9: RLS + triggers everywhere.
7. Day 10: Reconciliation jobs (snapshot rollover, variance detection, nightly mv refresh).

---

## 11. Open questions, deferred

- 多稅率 (5% / 0% 免稅) — currently `tax_amount` is a free field on orders. When we add 帳冊 export to 財政部, we'll need `tax_rates` and per-line tax_code.
- 套餐 / 加價購 — currently must be modeled as separate `menu_items`. When demand surfaces, add `menu_item_modifiers`.
- 多店成本歸屬 (HQ overhead allocation) — `fixed_cost_items.store_id` assumes one store owns the cost. Multi-store needs a split table.
- 損益的會計報表級別 (GAAP-friendly P&L) — `mv_daily_pnl` is operational, not statutory. Accounting export is a phase 2 module.

**This schema is the contract. Change it via ADR, not in-place edits.**
