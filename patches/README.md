# OpenSquilla 硬化 Patch

## `opensquilla-ws-origin-validation.patch` — 堵死 CSWSH → 主機 RCE（審計 F0）

修補 `01_security_audit.md` 的 F0：預設 `auth.mode=none` 下，任一惡意網頁可經
`ws://127.0.0.1:18791/ws` 劫持本地 agent、關掉沙盒、達成主機 RCE。

### 做了什麼

- 在 `handle_ws_connection` 的 `ws.accept()` **之前**加一道 Origin allowlist 檢查
  （`_ws_origin_allowed`）。外來 origin 的 WS 升級在觸及 auth 解析前就被 close(1008) 拒絕。
- allowlist = gateway 自身 loopback origin（`http(s)://127.0.0.1|localhost|[::1]:<port>`）
  + 操作者設定的 `control_ui.allowed_origins`（接上原本是空的死設定）。
- **無 Origin header 的 client（CLI / 桌面原生）照常放行**——瀏覽器一定送 Origin，原生不送。

### 為什麼不是「驗證 nonce」

握手的 nonce challenge 擋不住 CSWSH：攻擊者的 JS 讀得到 challenge frame 再回傳。
真正 load-bearing 的防禦是 Origin 檢查。此 patch 據實只做 Origin，不做會誤導、
還會弄壞現有 client 的 nonce-echo 假修補。

### 套用方式

```sh
# 在 opensquilla 的 fork 根目錄
git apply /path/to/patches/opensquilla-ws-origin-validation.patch
# 或
patch -p1 < /path/to/patches/opensquilla-ws-origin-validation.patch
```

基準 commit：`7f72a32`（main，2026-07-05）。已驗證於該 commit 乾淨套用、`py_compile` 通過。

### 驗證（純函式行為測試，7 例全綠）

| 情境 | 期望 |
|---|---|
| 無 Origin（CLI/原生） | 放行 |
| 同源 `http://127.0.0.1:18791` | 放行 |
| 同源 `http://localhost:18791` | 放行 |
| 設定的額外 origin | 放行 |
| **外來 `https://evil.example`（CSWSH）** | **拒絕** |
| loopback 但錯 port | 拒絕 |
| 字面 `"null"` origin | 拒絕 |

行為測試見 `test_ws_origin_guard.py`（可獨立跑，不需安裝整包）。

### 這不是全部

此 patch 只關 F0 的主鏈。仍建議搭配 `01_security_audit.md` 的其餘硬化：
`auth.mode=token`（縱深防禦）、收斂 CORS 預設、token 移出 query string。
完整清單見 `docs/opensquilla/02_fusion_plan.md` 的硬化 backlog。
