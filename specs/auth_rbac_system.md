# Spec: Auth & RBAC System (`/auth/*` + global deps)

> **Module name:** `restaurant_api.routers.auth` + `restaurant_api.models.auth` + `restaurant_api.services.auth_service` + `restaurant_api.services.rbac_service` + `restaurant_api.security.jwt` + extensions to `restaurant_api.api.deps`
> **Owner domain:** Platform / Security / Identity
> **Status:** Spec, ready for orchestrator hand-implementation (split into 5 PRs — see Implementation Plan)
> **Implementation target:** FastAPI router + global dependency layer + Alembic schema migration + data seed migration + CLI bootstrap script
> **Models touched (new):** `restaurant_api/models/auth.py` (UserCredential, Role, Permission, RolePermission, EmployeeRole, RefreshToken)
> **Models touched (modified):** `restaurant_api/models/employees.py` (back-populate `credential` + `roles` relationships only — no column changes)
> **Routers touched (modified):** `orders`, `stock_intake`, `clock`, `cost_events`, `events`, `line_webhook`, `health` (mount auth deps; **no** behavioural change)

---

## Background

目前 Phase 1 所有 `restaurant_api` 路由都標註「視為已通過上游 gateway 認證」，`tenant_id` 由 `X-Tenant-ID` header 帶入。這在 demo / 內網是 ok 的，**進入真實多店多人多角色**的 Phase 2 前必須先把認證 / 授權 / 多租戶隔離補齊，否則：

1. **任何能打到 internal port 的人都能偽造 tenant_id 拿到別人資料** — 違反個資法第 27 條（保有者應採行適當之安全措施）。
2. **無法區分老闆 / 店長 / 行銷 / 廚房 / 供應商** — 將來 KDS、行銷面板、供應商對賬入口無法做欄位級隔離。
3. **沒有可稽核的「誰在何時改了哪張單」** — 雖然 `audit_log` 表已落，但 `actor_id` 一直是 nullable，現實上一直空，違背 `docs/08_safety_compliance.md` §勞檢段的「打卡紀錄不可否認」要求。

本 spec 建立 **JWT-based stateless auth + 表驅動 RBAC** 的最小可用版本：employees 表保留現有 `role` enum（owner/manager/staff，**操作型角色** — 跟 HR 合約直接綁），新增 `roles` / `permissions` 表承載 **功能型角色**（與 employees.role 正交，允許「同一個 staff 同時是 marketing 又是 supplier 對接窗口」）。權限以 `resource:action` 字串表示（e.g. `orders:create`、`visual_assets:read_brand_ref`），全部走 DB 表，**不寫死在程式碼**。

JWT 走對稱式（HS256）作為 PoC，env var `JWT_SECRET` 控制 — 上 prod 換 RS256 並接 KMS 是另一個 spec 的事，本 spec 不處理 key rotation。Refresh token 走 **DB-backed + rotation**（一次性使用、用完即作廢、輪替發新的），原因：access token stateless 沒有 revoke 路徑，refresh 必須有 server-side state 才能「踢人」。

密碼用 **argon2id**（OWASP 2023 推薦），透過 `passlib[argon2]`。**禁止 bcrypt / pbkdf2 / sha** 等。

---

## Routes

| Method | Path | Purpose | Auth required |
|---|---|---|---|
| `POST` | `/auth/login` | email + password → access token + refresh token | **no** (public, rate-limited) |
| `POST` | `/auth/refresh` | refresh token → 新 access + 輪替後的 refresh | **no** (token itself is the credential) |
| `POST` | `/auth/logout` | revoke 指定 refresh token | **yes** (current user) |
| `GET`  | `/auth/me` | 回當前使用者 employee + roles + permissions | **yes** |
| `POST` | `/auth/change_password` | 舊密碼 + 新密碼 → 改密碼 + revoke 所有 refresh token | **yes** |

所有路由 prefix：`/auth`；OpenAPI tag：`auth`。
所有路由都注入 `session: AsyncSession = Depends(get_session)`。
所有 `/auth/login` 走 `slowapi` 限制：**5 requests / minute / IP**（key = `request.client.host`）。

### POST /auth/login

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `email` | `EmailStr` | yes | — | RFC 5322; lowercased before lookup |
| `password` | `SecretStr` | yes | — | 1..256 chars |

**Behaviour:**

1. Lookup `user_credentials` by `email = lower(input.email)`.
2. **不管找不找得到**，都跑 `passlib.verify()` 一次（用 dummy hash 若找不到 user），確保 response time 不洩漏 user 是否存在（timing attack 防護）。
3. 若 `locked_until > now()` → 423 Locked，回 `Retry-After` header（秒數）。
4. 若 `is_active = false` → 403 Forbidden，body `{"detail": "account disabled"}`。
5. 若 `failed_login_count >= 5` 且 `locked_until IS NULL or < now()` → 重置 `failed_login_count=0`（過了上次鎖期）。
6. 若密碼錯：`failed_login_count += 1`；若達 5 → `locked_until = now() + 15min`；回 401 `{"detail": "invalid credentials"}`。
7. 若密碼對：
   - `failed_login_count = 0`、`locked_until = NULL`、`last_login_at = now()`。
   - 生成 access JWT（15 min TTL，payload 見下）。
   - 生成 refresh token（32 bytes urandom → hex；DB 存 `sha256(hex)`；30 day TTL；`created_from_ip` / `user_agent` 填上）。
   - Response 200。

**Response (200 OK):** `LoginResponse`（見下方 Pydantic Schemas）。

### POST /auth/refresh

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `refresh_token` | `SecretStr` | yes | — | hex string, 64 chars |

**Behaviour (CRITICAL — rotation):**

1. `token_hash = sha256(input.refresh_token)`.
2. Lookup `refresh_tokens` by `token_hash`.
3. 若不存在 / `revoked_at IS NOT NULL` / `expires_at < now()` → 401 `{"detail": "invalid refresh token"}`.
4. **檢測重放（reuse detection）**：若該 token 已 revoked，**revoke 該 employee 的所有 active refresh tokens**（視為 compromise），回 401。
5. Mark 原 refresh token `revoked_at = now()`.
6. 生成新 refresh token（同樣 hash 後存 DB）+ 新 access token.
7. Response 200 same shape as `/auth/login`.

### POST /auth/logout

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `refresh_token` | `SecretStr` | yes | — | hex 64 chars |

**Behaviour:**

1. Require valid access token (via `get_current_user`).
2. Lookup refresh token; `revoked_at = now()`。即使找不到也回 204（避免揭露 token 有效性）。
3. Response 204 No Content。

### GET /auth/me

**Behaviour:** 走 `get_current_user`，回完整 user info + roles + flattened permissions。

**Response (200):** `MeResponse`。

### POST /auth/change_password

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `old_password` | `SecretStr` | yes | — | 1..256 chars |
| `new_password` | `SecretStr` | yes | — | 8..256 chars; must contain ≥1 digit + ≥1 letter |

**Behaviour:**

1. Require `get_current_user`.
2. Verify `old_password` against stored hash; 若錯 → 401 `{"detail": "invalid credentials"}`，並 `failed_login_count += 1` (共用 login 的鎖定邏輯).
3. Hash `new_password` (argon2id, default params); UPDATE `user_credentials.password_hash`、`updated_at`.
4. **Revoke 所有該 employee 的 active refresh tokens**（強制重新登入所有 device）。
5. 寫一筆 `audit_log` (action=`password_changed`, target_table=`user_credentials`).
6. Response 204 No Content.

---

## Pydantic Schemas

所有 input model：`ConfigDict(frozen=True)`（**不要** `strict=True`，會擋 JSON 的 UUID 字串 — 跟 CLAUDE.md 對齊）。
Response model：`ConfigDict(from_attributes=True)`；timestamps 以 `Asia/Taipei` zone-aware ISO8601 字串輸出。
密碼用 `SecretStr` 防止 log 洩漏。

```python
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

# --- requests ---

class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=256)

class RefreshRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    refresh_token: SecretStr = Field(min_length=64, max_length=64)

class LogoutRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    refresh_token: SecretStr = Field(min_length=64, max_length=64)

class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    old_password: SecretStr = Field(min_length=1, max_length=256)
    new_password: SecretStr = Field(min_length=8, max_length=256)
    # 不在 Field 寫 regex；在 model_validator 跑「含字母+數字」檢查，
    # 因為 SecretStr 不直接吃 pattern。

# --- responses ---

class TokenPair(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # access TTL in seconds (900)

class LoginResponse(TokenPair):
    pass

class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    employee_id: UUID
    tenant_id: UUID
    email: EmailStr
    display_name: str          # from employees.full_name
    employee_role: str         # from employees.role enum (owner/manager/staff)
    functional_roles: list[str]
    permissions: list[str]
    last_login_at: datetime | None

# --- internal dataclass (NOT a pydantic model — used inside deps) ---

from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class CurrentUser:
    employee_id: UUID
    tenant_id: UUID
    employee_role: str               # employees.role enum value
    functional_roles: frozenset[str] # role.name set
    permissions: frozenset[str]      # flattened set
    jti: str                         # JWT id, for revocation if needed
```

---

## JWT Design

### Access token (HS256, 15 min TTL)

Payload:

```json
{
  "sub": "<employee_id UUID>",
  "tenant_id": "<tenant_id UUID>",
  "employee_role": "manager",
  "roles": ["store_manager", "marketing"],
  "permissions": ["orders:create", "orders:close", "orders:read", "..."],
  "iat": 1719446400,
  "exp": 1719447300,
  "jti": "<UUIDv7>"
}
```

- `sub` / `tenant_id` / `jti` 必填且驗證；其餘缺 → 401。
- **Permissions 直接打進 token**：每個 request 不查 DB（效能）。代價：權限變更要等 ≤15min 才生效。可接受 — 緊急 revoke 走 refresh token + force change password。
- `aud` / `iss` 留空（PoC）；上 prod 再加。
- Encode/decode 用 `pyjwt`（不要用 `python-jose`，maintenance status 較差）。

### Refresh token (opaque, 30 day TTL)

- 32 bytes urandom → hex (64 chars)。
- DB 存 `sha256(hex)`，原文只在 response body 出現一次。
- 一次性：用過即 revoke、發新的（rotation）。
- 重放偵測：若已 revoked 的 token 被拿來換 → revoke 該 employee 全部 refresh token。

### Secret management

- `JWT_SECRET` 從 `restaurant_api.config.Settings`（pydantic-settings 已有）讀。
- Env var；長度 ≥ 32 chars，settings 啟動時驗證，否則 raise。
- Dev `.env` 用 dummy；prod 從 Cloudflare Tunnel 後面的 secret manager 注入。

---

## Database schema (new tables)

> 全部走一個 Alembic migration：`alembic/versions/0004_auth_rbac.py`。
> 所有表用 `models/base.py::Base` + `TimestampedMixin`，PK 走 `uuid7()`，金錢 N/A。

### `user_credentials`

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `uuid7()` |
| `employee_id` | `UUID` | FK → `employees.id`, **UNIQUE**, not null |
| `email` | `CITEXT` | UNIQUE, not null, lowercased on write |
| `password_hash` | `TEXT` | not null, argon2id format |
| `last_login_at` | `TIMESTAMPTZ` | nullable |
| `failed_login_count` | `INTEGER` | not null default 0 |
| `locked_until` | `TIMESTAMPTZ` | nullable |
| `is_active` | `BOOLEAN` | not null default `true` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | TimestampedMixin |

Index: `ix_user_credentials_email` (UNIQUE), `ix_user_credentials_employee_id` (UNIQUE).

### `roles`

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `name` | `VARCHAR(64)` | UNIQUE, not null, lowercased snake_case |
| `display_name` | `VARCHAR(128)` | not null |
| `description` | `TEXT` | nullable |
| `is_system` | `BOOLEAN` | not null default `false`; system roles 不可刪 |
| `tenant_id` | `UUID` | nullable (NULL = system role applicable across tenants) |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

Composite unique: `(tenant_id, name)`（tenant-scoped name uniqueness）。

### `permissions`

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `name` | `VARCHAR(128)` | UNIQUE, not null, format `^[a-z_]+:[a-z_]+$` |
| `description` | `TEXT` | nullable |
| `is_system` | `BOOLEAN` | not null default `true` (permission catalog 由 system 控) |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

### `role_permissions`

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `role_id` | `UUID` | FK → `roles.id`, ON DELETE CASCADE |
| `permission_id` | `UUID` | FK → `permissions.id`, ON DELETE CASCADE |
| `created_at` | `TIMESTAMPTZ` | |

Composite unique: `(role_id, permission_id)`.

### `employee_roles`

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `employee_id` | `UUID` | FK → `employees.id`, ON DELETE CASCADE |
| `role_id` | `UUID` | FK → `roles.id`, ON DELETE RESTRICT |
| `granted_by` | `UUID` | FK → `employees.id`, nullable (system grants = NULL) |
| `granted_at` | `TIMESTAMPTZ` | not null default `now()` |

Composite unique: `(employee_id, role_id)`.

### `refresh_tokens`

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `employee_id` | `UUID` | FK → `employees.id`, ON DELETE CASCADE |
| `token_hash` | `CHAR(64)` | UNIQUE, not null (sha256 hex) |
| `expires_at` | `TIMESTAMPTZ` | not null |
| `revoked_at` | `TIMESTAMPTZ` | nullable |
| `created_from_ip` | `INET` | nullable |
| `user_agent` | `TEXT` | nullable, max 500 chars |
| `created_at` | `TIMESTAMPTZ` | TimestampedMixin |

Index: `ix_refresh_tokens_employee_active` partial `WHERE revoked_at IS NULL`.

> **Note**：`refresh_tokens` **不**是 ledger（不像 stock_movements），允許 UPDATE `revoked_at`；但**不允許 DELETE**（保留歷史以支援 forensics）。後續可加 job 把 `expires_at < now() - 90day` 的 hard-delete。

---

## Database writes per endpoint

| Action | Tables written | Notes |
|---|---|---|
| `POST /auth/login` (success) | `user_credentials` (UPDATE last_login_at, failed_login_count=0, locked_until=NULL), `refresh_tokens` (INSERT 1) | one txn |
| `POST /auth/login` (wrong password) | `user_credentials` (UPDATE failed_login_count, maybe locked_until) | one txn |
| `POST /auth/login` (locked / disabled) | none | read-only |
| `POST /auth/refresh` (success) | `refresh_tokens` (UPDATE old revoked_at, INSERT new) | one txn |
| `POST /auth/refresh` (reuse detected) | `refresh_tokens` (UPDATE all of employee's active to revoked_at=now()) | one txn |
| `POST /auth/logout` | `refresh_tokens` (UPDATE revoked_at) | one txn |
| `GET /auth/me` | none | read-only |
| `POST /auth/change_password` | `user_credentials` (UPDATE password_hash), `refresh_tokens` (UPDATE all to revoked_at), `audit_log` (INSERT 1 via audit_service) | one txn |

---

## Global Dependencies (`api/deps.py` extensions)

新增 4 個 dep，全部 async：

```python
async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """從 Authorization: Bearer <jwt> 解析；驗簽、驗 exp、驗 sub 存在。
    失敗 → AuthError (401)。"""

def require_role(role_name: str):
    """Factory；returns dep that raises ForbiddenError if role_name not in user.functional_roles."""

def require_any_role(role_names: list[str]):
    """同上，但 'any of' 語意。"""

def require_permission(perm_name: str):
    """Factory；returns dep that raises ForbiddenError if perm_name not in user.permissions."""
```

### `get_current_tenant_id` 行為改變

**Before:**

```python
def get_current_tenant_id(x_tenant_id: UUID = Header(...)) -> UUID:
    return x_tenant_id
```

**After (transition mode):**

```python
async def get_current_tenant_id(
    request: Request,
    x_tenant_id: UUID | None = Header(default=None),
) -> UUID:
    # 1) JWT first
    user = getattr(request.state, "current_user", None)
    if user is not None:
        return user.tenant_id
    # 2) Header fallback (transition; will be removed in Phase 2.5)
    if x_tenant_id is not None:
        if settings.env != "dev":
            raise ForbiddenError("X-Tenant-ID header only allowed in dev")
        return x_tenant_id
    raise AuthError("no tenant context")
```

`current_user` 由 `get_current_user` 在跑完後寫入 `request.state.current_user`，後續 deps 重用、不重複解 JWT。

### Cross-tenant guard

每個 router 收到一筆需要 tenant scope 的 row 後，**必須**斷言 `row.tenant_id == current_user.tenant_id`，否則回 404（**不要回 403**，避免洩漏存在性）。本 spec 要求 `api/deps.py` 提供 helper：

```python
def assert_tenant_match(row_tenant_id: UUID, user: CurrentUser) -> None:
    if row_tenant_id != user.tenant_id:
        raise NotFoundError("resource not found")
```

---

## Existing router migration plan

每個 router 改裝清單（**PR-C** 範圍）：

| Router | Endpoint | 套用 dep | 備註 |
|---|---|---|---|
| `orders` | `POST /orders` | `require_permission("orders:create")` | tenant 從 JWT |
| `orders` | `GET /orders/{id}` | `require_permission("orders:read")` | + `assert_tenant_match` |
| `orders` | `POST /orders/{id}/close` | `require_permission("orders:close")` | |
| `orders` | `POST /orders/{id}/void` | `require_permission("orders:void")` | |
| `stock_intake` | `POST /stock_intake` | `require_permission("stock:intake")` | |
| `stock_intake` | `GET /stock_intake/{id}` | `require_permission("stock:read")` | |
| `clock` | `POST /clock/in` | `get_current_user` only（任何登入員工） | actor = current_user.employee_id，不接受 body 帶 employee_id |
| `clock` | `POST /clock/out` | `get_current_user` only | 同上 |
| `clock` | `GET /clock/today` | `require_permission("clock:read_self")` 或 manager 看全店 → `require_permission("clock:read_store")` | |
| `cost_events` | `POST /cost_events` | `require_permission("cost:create")` | |
| `cost_events` | `GET /cost_events` | `require_permission("cost:read")` | |
| `events` | `POST /events` | `require_permission("events:emit")` | |
| `health` | `GET /health/live` | **public** | 無 dep，無 logging |
| `health` | `GET /health/ready` | **internal-only**: 走 `X-Internal-Probe` shared secret header；不要走 JWT（k8s probe 拿不到 token） | |
| `line_webhook` | `POST /line/webhook` | **signature only**（既有 `X-Line-Signature` HMAC 驗證），**不**走 JWT；走完 sign 驗證後手動把 webhook 來源映射到一個 system service-account `CurrentUser`（tenant 從 LINE channel mapping 解析） | |

> **不**改 router 業務邏輯；只加 dep + tenant assertion。**任何 router test 在 PR-D 才補認證 fixture**。

---

## Permission catalog (seeded in PR-E)

20+ 個 permission，全部 `is_system=true`：

| name | description |
|---|---|
| `orders:create` | Create new orders |
| `orders:read` | Read orders within own tenant |
| `orders:close` | Close (settle) an order |
| `orders:void` | Void an order |
| `orders:refund` | (reserved for refund spec) Issue refund on closed order |
| `stock:intake` | Record purchase / intake into stock |
| `stock:read` | Read stock movements & on-hand |
| `stock:adjust` | Manual stock adjustment (loss / found / count) |
| `clock:read_self` | Read own clock-in/out records |
| `clock:read_store` | Read clock records of entire store |
| `clock:admin` | Edit / correct clock records (manager) |
| `cost:create` | Create cost events (utility / rent / etc) |
| `cost:read` | Read cost events |
| `visual_assets:read_brand_ref` | Read brand reference assets |
| `visual_assets:write` | Upload / edit visual assets |
| `events:emit` | Emit domain events |
| `auth:manage_users` | Create / disable user_credentials |
| `auth:manage_roles` | Grant / revoke roles to employees |
| `auth:reset_password` | Force reset another user's password |
| `admin:tenant_settings` | Edit tenant-level settings |
| `admin:audit_read` | Read audit_log |
| `admin:export_data` | Export tenant data dump |

### Role → permissions mapping (seeded)

| Role | Permissions |
|---|---|
| `owner` | **all** of the above (full access within tenant) |
| `store_manager` | orders:*, stock:*, clock:*, cost:*, events:emit, visual_assets:read_brand_ref, admin:audit_read |
| `marketing` | orders:read, visual_assets:read_brand_ref, visual_assets:write, events:emit |
| `supplier` | stock:read (limited — read-only view, scoped to supplier's own deliveries; cross-tenant scope handled separately) |
| `staff` | orders:create, orders:read, orders:close, clock:read_self |

---

## Bootstrap

第一次部署時資料庫沒任何 user 也沒任何 role。流程：

1. **Alembic data migration `0004_auth_rbac_seed.py`**（同一個 PR-A 內，跟 schema migration 鏈在一起）：
   - INSERT 22 個 system permissions（上表）。
   - INSERT 5 個 system roles（owner / store_manager / marketing / supplier / staff），`tenant_id=NULL`，`is_system=true`。
   - INSERT role_permissions 對應上表 mapping。

2. **CLI script `scripts/bootstrap_owner.py`**（PR-E 範圍）：
   ```bash
   python -m scripts.bootstrap_owner \
     --tenant-id <uuid> \
     --employee-id <uuid> \
     --email owner@example.com \
     --password-stdin
   ```
   - 必須先有 tenant + employee row 才能跑。
   - 從 stdin 讀密碼（不要走 argv 避免 ps 洩漏）。
   - 建 `user_credentials` row。
   - 自動 grant `owner` role。
   - Idempotent：若 email 已存在 → exit 1 with message「use --reset to override」。
   - 寫一筆 audit_log (action=`bootstrap_owner_created`)。

---

## Error responses

| Status | Trigger | Body |
|---|---|---|
| 400 | malformed JSON / missing required field | FastAPI default |
| 401 | invalid credentials (login) | `{"detail": "invalid credentials"}` |
| 401 | missing / malformed / expired JWT | `{"detail": "invalid token", "code": "invalid_token"}` |
| 401 | invalid / revoked / expired refresh token | `{"detail": "invalid refresh token"}` |
| 403 | account `is_active=false` | `{"detail": "account disabled"}` |
| 403 | JWT valid but missing required role | `{"detail": "insufficient role", "code": "forbidden_role"}` |
| 403 | JWT valid but missing required permission | `{"detail": "insufficient permission", "code": "forbidden_permission", "required": "orders:close"}` |
| 422 | Pydantic validation failure（含 email format、password length、new_password 強度） | FastAPI default |
| 423 | account locked (failed_login_count ≥ 5) | `{"detail": "account locked", "retry_after_seconds": N}` + `Retry-After: N` header |
| 429 | rate limit exceeded (login only) | `{"detail": "too many requests"}` + `Retry-After` header (slowapi default) |
| 500 | unexpected DB / crypto error | generic |

**Cross-tenant access**：回 404，**不**回 403（不洩漏存在性）。

---

## Acceptance Criteria

> 每一條對應一個 pytest test function；命名 `test_auth_ac_NN_*` 或 `test_rbac_ac_NN_*`。

### Login / credential

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | Login happy path | seed user + password → POST /auth/login → 200，body 含 `access_token`、`refresh_token`、`expires_in=900`，DB `last_login_at` 更新、`failed_login_count=0`。 |
| AC-2 | Login wrong password | POST with wrong password → 401 `invalid credentials`，DB `failed_login_count=1`。 |
| AC-3 | Account locks after 5 failures | 連續 5 次錯密碼 → 第 5 次 response 401，`locked_until = now()+15min`；第 6 次（即使正確）→ 423 `account locked` + `Retry-After` header。 |
| AC-4 | Lock auto-clears after window | 模擬 `locked_until` 過期（直接 UPDATE DB），下次正確密碼 → 200，`failed_login_count` 重設 0。 |
| AC-5 | Disabled account rejected | `is_active=false` → 403 `account disabled`，不更新 `failed_login_count`。 |
| AC-6 | Login is rate-limited | 同 IP 1 分鐘 6 次 login → 第 6 次 429 + `Retry-After`。 |
| AC-7 | Timing attack resistance | 對「存在的 user 給錯密碼」vs「不存在 user」量測 response time，差距 < 50ms（mean of 20 runs）。 |
| AC-8 | Email case-insensitive | seed `Owner@Example.COM`，用 `owner@example.com` login → 200。 |
| AC-9 | argon2id format | DB 中 `password_hash` 以 `$argon2id$` 開頭；長度 > 80 chars；不含明文。 |
| AC-10 | Password not in logs | 從 captured logs（log capture fixture）grep 不到 password plaintext，也不到 `password_hash`。 |

### JWT / token

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-11 | JWT contains required claims | decode access_token → 含 `sub`、`tenant_id`、`employee_role`、`roles`、`permissions`、`iat`、`exp`、`jti`，`exp - iat == 900`。 |
| AC-12 | Expired JWT rejected | 用 `jwt.encode` 手造一個 `exp = now()-1` 的 token → GET /auth/me → 401 `invalid_token`。 |
| AC-13 | Tampered JWT rejected | 把 valid token 的最後一個 char 改掉 → 401。 |
| AC-14 | Wrong secret JWT rejected | 用不同 secret 簽的 valid-shape token → 401。 |
| AC-15 | Missing Authorization header | GET /auth/me 不帶 header → 401 `invalid_token`。 |
| AC-16 | Malformed Authorization header | `Authorization: token xyz`（沒 Bearer）→ 401。 |

### Refresh / logout

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-17 | Refresh rotates token | POST /auth/refresh → 200，回新 `refresh_token`（值與原本不同）；DB 原 token `revoked_at` 非 null，新 token row 存在。 |
| AC-18 | Used refresh token rejected (rotation) | 連續用同一個 refresh_token 兩次 → 第二次 401，並 **該 employee 全部 active refresh token 被 revoke**（驗：DB 中該 employee 沒有 `revoked_at IS NULL` 的 row）。 |
| AC-19 | Expired refresh token rejected | 手動 UPDATE `expires_at = now()-1s` → 401。 |
| AC-20 | Logout revokes token | POST /auth/logout → 204；同一 refresh token 再 POST /auth/refresh → 401。 |
| AC-21 | Change password revokes all tokens | 改完密碼後，原 access token 在 TTL 內仍可用（stateless），但所有 refresh token 全部 `revoked_at` 非 null。 |

### RBAC / permission

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-22 | require_permission pass | 持有 `orders:create` 的 user 打 POST /orders → 201（其他驗證另算）。 |
| AC-23 | require_permission fail | 持有 `orders:read` 但無 `orders:create` 的 user 打 POST /orders → 403 `forbidden_permission`，response 含 `required: "orders:create"`。 |
| AC-24 | require_role pass | `store_manager` role 的 user 打需要該 role 的 endpoint → 200。 |
| AC-25 | require_any_role pass | user 有 `marketing` 沒 `store_manager`；dep `require_any_role(["marketing", "store_manager"])` → 200。 |
| AC-26 | Owner has all perms | `owner` role 的 user 對 22 個 system perm 都通過 `require_permission` 檢查。 |

### Tenant isolation

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-27 | Cross-tenant read → 404 | tenant A 的 user 拿 access token 去 GET /orders/{id_belonging_to_tenant_B} → 404（**不**是 403）；DB 沒被改。 |
| AC-28 | Cross-tenant write → 404 | 同上但 POST /orders/{B_id}/close → 404；B 的 order status 未動。 |
| AC-29 | tenant_id from JWT trumps header | 同時帶 valid JWT 和 `X-Tenant-ID: <different_uuid>` → 用 JWT 的 tenant_id，header 被忽略；dev mode warning log；prod mode 直接 403。 |
| AC-30 | No JWT no header → 401 | 完全不帶任何認證 → 401 `invalid_token`。 |

### Security / misc

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-31 | SQL injection in email | `email: "x' OR '1'='1"` → 422 (pydantic EmailStr) or 401 (lookup miss)；DB 無異常 query。 |
| AC-32 | New password strength | `new_password="abcdefgh"` (no digit) → 422；`"abc12345"` → 通過。 |
| AC-33 | change_password wrong old | 舊密碼錯 → 401，**不**改 hash，`failed_login_count += 1`。 |
| AC-34 | /auth/me returns flattened perms | `staff` role user → response `permissions` set = `{"orders:create","orders:read","orders:close","clock:read_self"}`。 |
| AC-35 | argon2id verify is constant-time | run 100 次 verify 對「正確密碼 vs 第一字元錯」量測，stddev < 20% of mean。 |
| AC-36 | LINE webhook bypasses JWT | POST /line/webhook with valid signature 不需 Authorization header → 200。 |
| AC-37 | /health/live is public | GET /health/live 不帶任何 header → 200。 |
| AC-38 | /health/ready needs internal probe | GET /health/ready 不帶 `X-Internal-Probe` → 403；帶正確值 → 200。 |
| AC-39 | Audit log on password change | 跑完 change_password → `audit_log` 表多 1 行 `action='password_changed'`、`actor_id=current_user.employee_id`。 |
| AC-40 | Bootstrap CLI idempotent | 對已存在的 email 跑 bootstrap → exit code 1，stderr 含 hint「use --reset」；DB 無改動。 |

---

## Tests

- 檔案位置：
  - `tests/routers/test_auth_router.py` — AC-1 ~ AC-21、AC-31 ~ AC-33、AC-39
  - `tests/test_jwt.py` — AC-11 ~ AC-16（純 unit，不打 HTTP）
  - `tests/test_rbac_deps.py` — AC-22 ~ AC-26、AC-30、AC-34（用 FastAPI dependency override 測 dep 行為）
  - `tests/test_tenant_isolation.py` — AC-27 ~ AC-29、AC-36 ~ AC-38（跨 router 整合）
  - `tests/test_password_security.py` — AC-7、AC-9、AC-10、AC-35（含 timing 量測，用 `time.perf_counter_ns`）
  - `tests/scripts/test_bootstrap_owner.py` — AC-40
- 框架：`pytest` + `pytest-asyncio` + `httpx.AsyncClient` + `ASGITransport`（**不**用 sync TestClient — event loop 衝突）。
- DB：每 test 一個 SAVEPOINT，跑完 rollback（沿用 `tests/conftest.py` 既有 fixture）。
- 新增 fixture（PR-D 範圍，放 `tests/conftest.py`）：
  - `seed_user_credential(employee, password) -> UserCredential`
  - `seed_role(name, permissions: list[str]) -> Role`
  - `grant_role(employee, role)`
  - `login_as(client, employee) -> dict[str, str]` — 回 headers dict 含 `Authorization: Bearer <token>`
  - `auth_client(client, employee) -> AsyncClient` — 預設帶好 header 的 client
  - `seeded_owner` / `seeded_store_manager` / `seeded_staff` / `seeded_marketing` / `seeded_supplier` — 5 個常用角色 fixture
- 既有所有 router test（37 個）在 PR-D 改用 `auth_client(seeded_store_manager)` 預設，**不**直接打沒帶 token 的 endpoint。
- Coverage 目標：`restaurant_api.routers.auth`、`restaurant_api.services.auth_service`、`restaurant_api.services.rbac_service`、`restaurant_api.security.jwt` line coverage ≥ 95%；其他 router migration 部分維持原 coverage 不退步。

---

## Out of scope

- **OAuth2 / OIDC / SSO**（Google login、LINE login）：另開 spec；本 spec 純 email+password。
- **MFA / TOTP / WebAuthn**：另開 spec；password 強度要求只到「字母+數字+長度」。
- **API key authentication**（給第三方 POS round-trip 用）：另開 spec；本 spec 只給人類用。
- **IP allowlist / 地理限制**：另開 spec；目前只靠 Cloudflare WAF。
- **Audit log of permission grants/revokes**：MVP 只 audit 自身（password_changed、bootstrap_owner_created）；grant_role / revoke_role / create_user 的 audit 留給 「Auth admin router」spec。
- **Self-service registration**：本 spec 不開放公開註冊；user 一律由 owner / `auth:manage_users` 權限者建立。
- **Email verification / password reset via email**：本 spec 純內部，密碼忘了走「owner reset」或 CLI。
- **Session revocation UI / device list**：另開 spec；DB 結構 (`refresh_tokens.user_agent`、`created_from_ip`) 已預留。
- **Permission inheritance / role hierarchy**：所有 role 平起平坐；無 parent role 概念。
- **Per-store permission scoping**：本 spec 的 permission 是 tenant-level；「店長 A 只能管 store-1」屬於 row-level security，另開 spec。
- **Key rotation / RS256 with JWKS**：PoC 走 HS256；prod 上線前另開 spec。
- **Cookie-based session**：本 spec 純 Bearer token；不發 cookie、不處理 CSRF。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `restaurant_api.models.employees` | `UserCredential.employee_id` 1:1 FK；建 credential 時必須對應現存 employee row（不存在 → 422）。Employees 表 **不**動 column，只加 ORM 端 relationship `back_populates="credential"`、`secondary="employee_roles"`. |
| `restaurant_api.services.audit_service` | `auth_service.change_password` 結束前呼叫 `audit_service.audit(action="password_changed", actor_id=current_user.employee_id, target_table="user_credentials", target_id=cred.id, before=None, after=None)`。grant/revoke role 的 audit 留給下個 spec。 |
| `restaurant_api.api.deps` | 擴充 `get_current_user` / `require_role` / `require_permission` / `require_any_role` / `assert_tenant_match`；改寫 `get_current_tenant_id` 走 JWT-first。 |
| `restaurant_api.api.errors` | 新增 `AuthError(401)`、`ForbiddenError(403)`、`LockedError(423)` 三個 DomainError 子類；既有 `NotFoundError` 沿用。 |
| `restaurant_api.config.Settings` | 新增 `jwt_secret: SecretStr` (min_length=32, validated at boot)、`jwt_access_ttl_seconds: int = 900`、`jwt_refresh_ttl_days: int = 30`、`internal_probe_secret: SecretStr`（給 /health/ready）。 |
| `restaurant_api.middleware` | `RequestContextMiddleware` 在解完 JWT 後把 `current_user.employee_id`、`current_user.tenant_id` 塞進 `request.state` + 結構化 log 的 `extra`（讓所有後續 log 自動帶 actor）。 |
| `restaurant_api.integrations.line` | LINE webhook 簽章驗完後，產一個 system-account `CurrentUser`（特殊 employee row `system@line-webhook`）放進 request.state，後續業務邏輯就走相同的 tenant / permission 框架。 |
| `restaurant_api.jobs` | APScheduler 跑的 job（expiry/points/COGS）目前無 actor；本 spec 不動 job，但 audit_service 需要支援 `actor_id=None`（system actor）— 已支援，無變更。 |
| `mv_daily_pnl` (DB view) | N/A — view 與 auth 無關。 |
| `devswarm` | 完全無關 — DevSwarm 跑在 workspace 沙盒，不打 restaurant_api。 |

---

## Implementation Plan (5 PRs)

> **不要做成一個巨型 PR**。每個 PR 必須能獨立 merge、CI 全綠、不打破現有功能。
> 每個 PR 末尾 commit message 標 `Part X/5 — auth_rbac_system`，PR description 連結本 spec。

### PR-A: Schema migration + ORM models (≈ 2 天)

**範圍：**
- `restaurant_api/models/auth.py` 新檔，6 個 SQLAlchemy class（UserCredential, Role, Permission, RolePermission, EmployeeRole, RefreshToken）。
- `restaurant_api/models/employees.py` 加 `credential = relationship("UserCredential", uselist=False, back_populates="employee")`、`roles = relationship("Role", secondary="employee_roles", viewonly=True)`，**不**動既有 column。
- `restaurant_api/alembic/versions/0004_auth_rbac.py` schema migration。
- `restaurant_api/alembic/versions/0005_auth_rbac_seed.py` data migration：22 permissions + 5 system roles + role_permission mapping。
- 新 unit test：`tests/test_models_auth.py` — 驗證 ORM relationship、unique constraint、FK cascade。

**驗收：** `make full-check` 全綠；`alembic upgrade head` + `alembic downgrade -1` round-trip 無錯；新表透過 ORM 可建可刪可查；seed migration 在乾淨 DB 跑出正確 22 + 5 + N 行。

**Out:** 完全沒有 API endpoint、沒有 JWT、沒有 deps 改動。

### PR-B: JWT + auth router + deps (≈ 3 天)

**範圍：**
- `restaurant_api/security/jwt.py` 新檔：encode / decode / 自訂 claims。
- `restaurant_api/services/auth_service.py` 新檔：login / refresh / logout / change_password / verify_password / hash_password。
- `restaurant_api/services/rbac_service.py` 新檔：load_user_context (employee → CurrentUser dataclass)、`grant_role` / `revoke_role`（internal API；endpoint 留給下個 spec）。
- `restaurant_api/routers/auth.py` 新檔：5 個 endpoint。
- `restaurant_api/api/deps.py` 擴充：`get_current_user`、`require_role`、`require_any_role`、`require_permission`、`assert_tenant_match`；**先**並存 `get_current_tenant_id` 的舊行為（header）+ 新行為（JWT），用 settings flag `auth_enforcement: Literal["off","warn","enforce"] = "warn"` 控。
- `restaurant_api/api/errors.py` 加 3 個新 exception。
- `restaurant_api/config.py` 新 settings field。
- Tests: `test_auth_router.py`、`test_jwt.py`、`test_rbac_deps.py`、`test_password_security.py`（覆蓋 AC-1 ~ AC-26、AC-31 ~ AC-35、AC-39）。
- 既有 router **不改**（still header-based），`auth_enforcement="warn"` 預設只 log warning。

**驗收：** 上述 AC 對應 test 全綠；既有 37 個 router test 不動且全綠；`make full-check` 全綠。

**Out:** 既有 router 沒接 dep；沒有 bootstrap CLI；LINE webhook 沒動。

### PR-C: Migrate existing routers to auth (≈ 3 天)

**範圍：**
- 按上方 migration table 改每個 router 的 dep。
- `settings.auth_enforcement` 預設改 `"enforce"`。
- `get_current_tenant_id` 移除 prod 模式的 header path（dev 保留）。
- `RequestContextMiddleware` 改成 inject `current_user`。
- LINE webhook 加 system-account 邏輯。
- `/health/live` 拔掉所有 dep；`/health/ready` 加 `X-Internal-Probe` 驗證。
- **暫時**讓所有既有 router test fail（因為沒帶 token）— 在 PR-D 修。
- Coverage: AC-27 ~ AC-30、AC-36 ~ AC-38 對應 test 新增。

**驗收：** `tests/routers/*` 暫時整批 fail 是可接受的（PR description 明寫「by design, fixed in PR-D」）；但 `tests/test_tenant_isolation.py` 全綠；ruff / pyright 全綠。

**警告：** 這個 PR 是 **breaking change**；merge 前在 staging deploy 並用 PR-E bootstrap CLI 建 owner 確認可登入。

### PR-D: Test fixture overhaul + router tests re-greened (≈ 2 天)

**範圍：**
- `tests/conftest.py` 加 fixture：`seed_user_credential`、`seed_role`、`grant_role`、`login_as`、`auth_client`、5 個 role-specific user fixture。
- 全部 37 個 router test 改用 `auth_client(seeded_store_manager)` 或更精確的 role fixture。
- 對每個 router 補一條 「無權限被拒」test（e.g. `test_orders_no_permission_blocked`）。
- 對每個 router 補一條 「跨 tenant 被拒」test。

**驗收：** 全部 router test 重新全綠；新增至少 14 條（7 router × 2）「拒絕」test 也全綠；`make full-check` 全綠。

### PR-E: Bootstrap CLI + permission catalog finalize (≈ 1 天)

**範圍：**
- `scripts/bootstrap_owner.py` 新檔。
- `tests/scripts/test_bootstrap_owner.py` 新檔（覆蓋 AC-40）。
- `Makefile` 新 target：`bootstrap-owner`、`bootstrap-owner-help`。
- `docs/12_auth_operations.md` 新檔（**只**在 PR-E 寫，**不**在前面 PR 寫；內容：怎麼建第一個 owner、怎麼 reset 密碼、怎麼 revoke session、JWT secret rotation 注意事項）。
- `COMMANDER_HANDOFF.md` 更新一節「auth 上線後的 day-2 SOP」。

**驗收：** `make bootstrap-owner` 能在乾淨 DB（跑完 PR-A 的 migration 後）建出第一個 owner 並登入成功；AC-40 綠；文件能讓非作者照著做不卡關。

---

## Constraints (hard requirements)

- Python 3.12 + 既有 stack（FastAPI 0.136 / SQLAlchemy 2.x / asyncpg / Pydantic 2.5+）
- 新增依賴：`passlib[argon2]>=1.7.4`、`pyjwt[crypto]>=2.9`、`slowapi>=0.1.9`（寫進 `pyproject.toml`，PR-A 一起加）
- **不用** bcrypt、**不用** python-jose、**不用** flask-login、**不用** authlib
- Pydantic input model：`ConfigDict(frozen=True)` only — **never** `strict=True`（會擋 JSON 的 UUID 字串，跟 CLAUDE.md 對齊）
- 密碼欄位用 `SecretStr`，**禁止**寫進任何 log / repr / DB raw
- 所有 timestamp tz-aware UTC（DB 存 UTC、response 渲 Asia/Taipei）
- 所有 PK UUIDv7（用 `models/base.uuid7()`）
- DomainError → HTTP 經 `api/errors.py` 統一映射；router **禁止** raw `HTTPException`
- 結構化 log 經 `logger.info("auth.login.success", extra={...})`；event name 用 `auth.<action>.<outcome>` 命名
- 寫稽核紀錄走 `audit_service.audit()`，不直接 INSERT AuditLog
- 路由整合測用 `httpx.AsyncClient + ASGITransport`（**禁** sync TestClient）
- 每 test 一個 SAVEPOINT + rollback；不依賴 truncate
- ruff / pyright / pytest / alembic-check 全綠才能 merge；`make full-check`

---

## 給 PM Agent 的提醒

- **不要把 employees.role 跟 functional roles 混在一起**。employees.role 是 HR 合約屬性（決定薪資、勞檢分類），跟系統權限**完全正交**。本 spec 加的 roles 表是後者。owner 員工合約上是 owner，但他系統上可能還掛 `marketing` 功能 role；staff 員工合約是 staff，但能被指派 `store_manager` 功能 role 代理店長。
- **Refresh token rotation 的 reuse detection 是 auth 安全的關鍵**。看到「reuse」必須假設 token 被偷，整批 revoke。這條 AC-18 不可降級。
- **timing attack 防護**（AC-7）很常被忽略，務必在 PM 階段強調「不管 user 存不存在都跑一次 verify」。
- **cross-tenant 回 404 不是 403**（AC-27/28）。這是行業慣例 — 回 403 等於告訴攻擊者「這個 id 真的存在於別人那」。
- **LINE webhook 不走 JWT**（AC-36）。LINE 平台不會替我們帶 token。它走 HMAC 簽章，本 spec 只負責把簽章驗完後產 system-account `CurrentUser`，業務邏輯共用同一套 RBAC 框架。
- **`get_current_tenant_id` 改造有過渡期**。直接從 header 改 JWT-only 會打斷所有現有 test。PR-B 並存、PR-C 切換、PR-D 修 test — 這個順序不能跳。
- **bootstrap 是 chicken-and-egg**：沒 user 怎麼建第一個 user？只能用 CLI（PR-E）+ data migration（PR-A）兩段式。**不要**做成 HTTP endpoint，會永遠開著被打。
- **permission catalog 直接寫進 data migration**，不要做動態管理 UI（那是另一個 spec）。Phase 2 只有 owner 能 grant/revoke role，且走另外的 admin spec，本 spec 不開該 endpoint。
- **JWT 內嵌 permissions 的代價**：權限變更要 ≤15min 才生效（access token 自然過期）。緊急踢人走「revoke all refresh tokens + 等 access 自然過期 + force change password」三招組合。這個 trade-off 是有意的，PR 審核時不要被質疑成 bug。
- **Phase 2 後的 audit / admin / SSO 都是分開的 spec**，本 spec 嚴守邊界，看到「順手加一下」的 scope creep 必須拒絕。

— end of spec —
