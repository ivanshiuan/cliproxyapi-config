# 20 — Auth 上線 Day-2 SOP

> 這份是給 Ivan、營運長、輪班運維工程師看的。內容：怎麼在乾淨的
> Postgres 上長出「第一個 owner」、怎麼幫新員工開通、怎麼撤權、
> 怎麼 rotate secret、遇到問題怎麼救。
>
> **前提**：`docs/19_operating_system_playbook.md` 已讀。這份是操作手冊，
> 那份是策略書。

---

## 0. Quick reference

| 想做 | 指令 |
|---|---|
| 建第一個 owner | `make bootstrap-owner TENANT=<uuid> EMPLOYEE=<uuid> EMAIL=x@y` |
| 幫既有員工開通登入 | 同上（無論第幾個都用這個） |
| 撤某人 role | `POST /auth/manage_roles`（PR-E 之後 spec）|
| 強制某人重新登入 | `DELETE` 該 employee 的 `refresh_tokens`（DB 直查）|
| 全站鎖死 | `RESTO_AUTH_ENFORCEMENT=off` → `enforce`（滾動更新）|
| JWT secret 換掉 | 改 `RESTO_JWT_SECRET` env → 重啟 → 所有現有 token 失效 |

---

## 1. 系統首次上線流程（Day 1）

```
   docker-compose up postgres redis
     │
     ▼
   make db-migrate            # 跑到 head，包含 auth schema + seed
     │
     ▼
   python scripts/seed_demo_data.py    # 建 1 tenant + 5 員工
     │
     ▼
   把 owner 那位員工的 UUID 記下來（seed script 印出來）
     │
     ▼
   make bootstrap-owner \\
     TENANT=<tenant_uuid> \\
     EMPLOYEE=<owner_employee_uuid> \\
     EMAIL=ivan@myrestaurant.tw
   （會 prompt 你打密碼；打完不會 echo）
     │
     ▼
   curl -X POST http://localhost:8000/auth/login \\
     -H 'Content-Type: application/json' \\
     -d '{"email":"ivan@myrestaurant.tw","password":"..."}'
   → 拿到 access_token + refresh_token
```

從這步起，一切走 `Authorization: Bearer <access_token>`。

---

## 2. 加新員工（Day-N）

新員工上工三步：
1. **HR 建 employee row**（`POST /employees` — Phase 3 endpoint，MVP 手動 INSERT）
2. **Bootstrap 建 credential**：
   ```bash
   make bootstrap-owner \\
     TENANT=... EMPLOYEE=<new-employee-uuid> \\
     EMAIL=<new-employee-email>
   ```
   （雖然 target 叫 bootstrap-owner，其實對任何 employee 都能用；只有第一次會被 grant owner role — 之後預設不 grant，走下一步）
3. **Grant 對應 role**（Phase 3 admin UI；PoC 階段直接 SQL INSERT `employee_roles`）

> **常見錯誤**：忘了第 3 步 → 員工能登入但一操作就 403。
> 記在 `docs/19_operating_system_playbook.md` §4.1 每日 SOP。

---

## 3. 撤某人的權限（離職 / 調職）

**三選一：**

### 3.1 最徹底（離職）
```sql
UPDATE user_credentials SET is_active = false
  WHERE employee_id = '<uuid>';
UPDATE refresh_tokens SET revoked_at = now()
  WHERE employee_id = '<uuid>' AND revoked_at IS NULL;
UPDATE employees SET deleted_at = now(), terminated_on = current_date
  WHERE id = '<uuid>';
```
- 立即：login 拒；refresh 拒（也順便強制斷開所有現存 session）
- 待清理：employee_roles rows 保留供稽核（**不要 DELETE**）

### 3.2 暫時停權（外派 / 留職停薪）
```sql
UPDATE user_credentials SET is_active = false
  WHERE employee_id = '<uuid>';
```
- Login 拒；access_token 有效但 15 分鐘內失效
- 復職：改回 `is_active = true`

### 3.3 只撤某個 role
```sql
DELETE FROM employee_roles
  WHERE employee_id = '<uuid>' AND role_id = (
    SELECT id FROM roles WHERE name = '<role-name>' AND tenant_id IS NULL
  );
```
- 立即：新 JWT 不含該 role；舊 access_token 15 分鐘後失效

---

## 4. 遇到問題怎麼救

| 症狀 | 診斷 | 解法 |
|---|---|---|
| 全公司都 401 | JWT_SECRET 被換 / config 不對 | 檢查 `env | grep RESTO_JWT_SECRET`、`env | grep RESTO_AUTH_ENFORCEMENT` |
| 某人明明有 role 卻 403 | JWT 內 permission 快取（15min TTL）| logout + login 拿新 token |
| 我改了 permissions 但沒生效 | 同上 | 影響大時 revoke 所有 refresh：`UPDATE refresh_tokens SET revoked_at=now() WHERE revoked_at IS NULL` |
| 帳號鎖住了 | 5 次失敗 → 15 分鐘 lockout | 等 15 分鐘 OR `UPDATE user_credentials SET locked_until=NULL, failed_login_count=0 WHERE email='...'` |
| 忘密碼 | 沒 SMS 重設（Phase 3 才有） | owner 直接 bootstrap 覆寫（refuses if email mismatch，要保留同 email）|
| /health/ready 一直 503 | DB 連線壞 / 忘了帶 X-Internal-Probe | 檢查 `docker-compose logs postgres` 跟 K8s manifest 的 header |

---

## 5. 定期維運

### 5.1 每天（早上開店）
- 看昨天 audit_log 有沒有 role grant / password_changed 異常
- 看 refresh_tokens 表有沒有非預期的 IP / user_agent

### 5.2 每週
- 跑「離職員工 role check」— SQL:
  ```sql
  SELECT e.full_name, r.name AS role, er.granted_at
    FROM employee_roles er
    JOIN employees e ON e.id = er.employee_id
    JOIN roles r ON r.id = er.role_id
   WHERE e.terminated_on IS NOT NULL
     AND e.terminated_on < current_date;
  ```
  應該回 0 行。有 → 遺漏了。

### 5.3 每 90 天
- **Rotate JWT_SECRET**：
  1. 產生新 32-char secret：`openssl rand -hex 32`
  2. 存進 secrets manager（Cloudflare / 1Password / Vault）
  3. 更新 env、rolling restart
  4. 所有現存 access_token 立即失效（15 分鐘內全部使用者被踢回登入頁）
  5. Refresh_token **沒被 rotate**（它們是 opaque bytes、不簽章）— 用戶登入後拿新 access

### 5.4 每年
- 完整 audit_log 抽驗
- 每個 role → permission mapping 對照 permission catalog spec，確認沒漂移
- 走一次 §4 表格，確認每種救援場景都能執行

---

## 6. 安全紀律（做/不做）

### ✅ 一定要做
- 每次 grant/revoke role 前，記錄理由到你的 Ivan-only Notion
- production 一定改 `RESTO_JWT_SECRET`（預設是 dev-insecure 字串會 loud fail）
- 每個新加盟主 tenant，第一次 bootstrap-owner 由 Ivan 現場執行
- audit_log 30 天備份（走既有 PG backup）

### ❌ 絕對不做
- 在 git commit 或 Slack 傳明文密碼 / JWT secret
- 把 `RESTO_AUTH_ENFORCEMENT=off` 留在 prod（除非全站緊急降級）
- 為了「方便」把 owner role 給非 owner 的人
- 硬改 `refresh_tokens` 或 `audit_log` 用 UPDATE / DELETE
- 用同一個 email 對兩個 employee（credential email UNIQUE 已擋，社交攻擊要注意）

---

## 7. 相關文件

- `docs/19_operating_system_playbook.md` — 為什麼要做這一切、對外怎麼講
- `specs/auth_rbac_system.md` — 完整技術 spec
- `docs/08_safety_compliance.md` — 台灣合規 SOP（食安 / 勞檢 / 個資）
- `docs/11_production_deployment.md` — 部署 SOP
- `COMMANDER_HANDOFF.md` — 指揮官交接清單

—— end of 20_auth_operations.md ——
