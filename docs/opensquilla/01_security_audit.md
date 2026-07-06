# OpenSquilla 安全審計報告

> 對象：opensquilla/opensquilla `main`（commit `7f72a32`，238k LOC / 775 Python 檔），完整 clone 逐檔審。
> 方法：4 個平行審計（沙盒 / 密鑰 / 供應鏈 / 網路）+ 2 個對抗性覆核（紅隊試推翻 + 界定真實破壞力）+ 人工驗證關鍵鏈路。
> 日期：2026-07-05

---

## 裁決

**有條件 GO。** 安全底子（尤其沙盒）扎實到值得當內核，但**預設組態有一個已三方確認、可達主機 RCE 的 critical**。fork 來研究/開發沒問題；**在關掉這個洞之前，絕不接真 API key、絕不日常掛著跑**。

## 四大面向總評

| 面向 | 評級 | 結論 |
|---|---|---|
| 沙盒 / 命令執行 | 🟢 強 | 真隔離、fail-**closed**；但不兜底下述 CSWSH |
| 密鑰處理 | 🟡 中 | env 優先、全面 redact、prompt 乾淨；at-rest 仍明文無 keychain |
| 供應鏈 / 安裝 | 🟡 中 | 全官方網域、非 root；wheel 無雜湊校驗、curl\|sh |
| 網路暴露面 | 🔴 **Critical（RCE）** | 預設 no-auth 下惡意網頁可劫持 agent → 關沙盒 → 主機 RCE |

---

## 🔴 F0 — CSWSH → 主機 RCE（Critical，雙 agent 確認）

**跨站 WebSocket 劫持，與 OpenClaw 的 ClawJacked 同類，在預設組態下原樣存在，且可升級為完整主機 RCE。**

### 攻擊鏈（預設組態即成立）

1. 受害者本機跑著預設 opensquilla（`auth.mode=none` + 綁 `127.0.0.1:18791`），瀏覽器開了任一惡意網頁。
2. 該頁 JS 執行 `new WebSocket("ws://127.0.0.1:18791/ws")`。瀏覽器對 WS 不套同源限制；`websocket.py:555` 的 `ws.accept()` 無條件接受，全 gateway 無 Origin 檢查，`middleware.py:33-34` 的 `AuthMiddleware` 對 WS upgrade 直接放行（註解自承「WS handles own auth」）。
3. 送一個空 `auth` 的 `connect`。握手發的 nonce challenge（`websocket.py:559-561`）是**死欄位**——全 repo 從不比對。no-auth 的 `OpenScopeResolver`（`auth.py:132-166`）僅憑「loopback 對端 + loopback 綁定」這個**瀏覽器天然滿足**的條件，發 **owner + 完整 admin**（`CLI_DEFAULT_OPERATOR_SCOPES` = admin/read/write/approvals/proposals/pairing，`scopes.py:43-48`），不需任何秘密。
4. 攻擊者以 owner 呼叫 `sandbox.run_context.set` 設 `runMode="full"`（只需 write scope，owner 有；`rpc_sandbox.py:387-393`）或打 `/api/elevated-mode`（只檢 `is_owner`；`app.py:405`）→ **合法關掉沙盒**。`run_mode_policy.py:9-25`：owner 被允許 FULL，且預設 run mode 對 owner 即 FULL。
5. 再驅動 agent 的 shell 工具——命令此時**在主機上跑**（`shell.py:331-333, 3521`），且 owner 身分**自動核准所有待審**（`app.py:422-428`），無第二道人工關卡 → **主機任意指令執行**。

### 為何比一般 CSWSH 嚴重

不止能讀光所有 session/記憶/對話、改 `llm.base_url` 把後續 LLM 流量導去攻擊者端點、裝持久化 skill/cron——最終能直接 RCE。**沙盒不兜底**，因為 owner 本來就是被設計允許停用沙盒的身分，而這個洞免費奉送 owner。

### 對抗性覆核結果

- **紅隊 agent**：從五個角度試圖推翻（middleware 是否擋 WS / 是否有惡意站無法提供的欄位 / 能否讀回回應 / 預設是否真 no-auth / nonce 是否別處驗證），**全部失敗**。裁定 CONFIRMED，且無前置條件（不需受害者先開過 Control UI）。
- **破壞力 agent**：確認定級正確甚至略微低估——可達 host RCE。

### 既有防線（擋得住什麼、擋不住什麼）

- ✅ 擋住「一次偷 raw API key」：`secrets.resolve`/`reload` 是停用 stub（`rpc_secrets.py:12-26`）；`config.get` 的 key 欄位被 redact（`config.py:2255`）。
- ❌ 擋不住：讀光 session/記憶、改 `llm.base_url` 外送流量、裝持久化程式碼、**主機 RCE**。

### ✅ 完整緩解（關鍵）

設 **`auth.mode=token`**：同一握手走 `TokenScopeResolver`，惡意站沒 token → `raise`（`auth.py:87-89`）→ `resolve_auth` 回 None → 連線**直接關閉，連受限 principal 都不發**（`websocket.py:611-614`）。惡意站能力歸零。**這是危險的預設值，不是無解漏洞——一行 config 就堵死。**

### 修補（見 `patches/opensquilla-ws-origin-validation.patch`）

1. WS 握手在 `ws.accept()` 前/後加 **Origin allowlist** 檢查（接上目前空的死設定 `control_ui.allowed_origins`，`config.py:109`）。
2. 讓 **nonce 真的被驗證**（要求 client 回簽）。
3. 收斂 CORS 預設（`allow_origins=["*"] + allow_credentials=True` → 明確 loopback allowlist）。
4. token 移出 query string 或關閉 uvicorn access_log 對 query 的記錄。

---

## 🟢 沙盒（強，值得保留）

- **真隔離**：Linux 走完整 namespace unshare（user/pid/uts/ipc/cgroup/net）+ `--cap-drop ALL` + `--clearenv` + tmpfs 根 + `--unshare-net`；開機還實跑一次 `bwrap --unshare-user --unshare-net` 驗證 namespace 可用。macOS Seatbelt `(deny default)` + `(deny network*)` + 寫入白名單 + `.git` 元資料保護。
- **三層策略有實質差異**：Locked = 唯讀 workspace + 無可寫 /tmp + 零額外掛載 + 無網路 + 強制人工核准 + 最緊資源上限。不是換標籤。
- **Fail-CLOSED**：沒裝 bwrap（或 namespace 不可用）→ 開機 raise 或每次執行 raise（`UnavailableBackend`）。想裸跑必須明確 `sandbox=false` 且每次印 `WARNING sandbox.bypass`。沙盒擋下的指令**不回退 host**。
- **Path traversal 多層防護**：resolve-then-contain + 敏感前綴封鎖（`/etc /proc /sys /dev /root`、docker.sock）+ 掛載層再擋字面 `..`。
- 唯一弱點：正則 denylist 粗糙可繞——但只是縱深防禦，繞過後指令仍在沙盒內，低風險。

## 🟡 密鑰處理（中）

- ✅ 好：env 來源的 key **永不寫進 config.toml**；config 寫入原子化（mkstemp→0600→os.replace）+ backup；**系統 prompt 不含任何密鑰**；記憶入庫前 redact（打 OpenClaw「從 prompt/記憶抽憑證」那條鏈）；doctor/log/RPC 全面遮蔽；migrate 明確拒絕整包搬 OpenClaw 的 credentials/identity/device 檔。
- ❌ 弱：**at-rest 仍明文、無 OS keychain**——與 OpenClaw 被 infostealer 竊設定檔同一暴露面，只縮小未根除。`migrate --migrate-secrets` 兩個殘留：`.env` write-then-chmod 競態且靜默吞 chmod 失敗（跑完 `ls -l` 確認 0600）；歸檔目錄把 `webhooks/ bindings/ hooks/` 未 redact 原樣複製（跑完要清）。

## 🟡 供應鏈 / 安裝（中）

- ✅ 好：全程只連 github/pypi/astral.sh/aka.ms 官方網域；Docker 非 root（uid 10001，`USER opensquilla`）；systemd 全 user-scope loopback 非特權；依賴無 typosquat、無 git 直連；帶 secrets 的 CI 只手動觸發（fork PR 無法外洩 secrets）；`pull_request_target` 寫法正確。
- ❌ 弱：**wheel 無雜湊校驗**（release 有出 `SHA256SUMS` 但兩個 installer 從不比對）；依賴無 hash pin（`>=` 浮動、不用倉庫內 `uv.lock`）；`curl|sh` bootstrap；Windows `start.ps1` 下載 `vc_redist.x64.exe` 後 UAC 提權執行且無雜湊；third-party actions 只 tag pin 未 SHA pin。
- **建議**：不要無條件 `curl … | sh`。先 `curl -o install.sh` 人工看過（確認 `wheel_url` 指向官方、`OPENSQUILLA_REPOSITORY`/`OPENSQUILLA_VERSION` 未被預設成非官方值），或用內建 `OPENSQUILLA_INSTALL_DRY_RUN=1` 先看會跑什麼。

---

## 安裝前硬性檢查清單（接真 key 前必做）

- [ ] `auth.mode=token`（堵死 F0 的 RCE 鏈）
- [ ] 維持綁 `127.0.0.1`，**不綁 `0.0.0.0`**
- [ ] 第一週沙盒開 `Strict`
- [ ] 若用過 `--migrate-secrets`：清 `~/.opensquilla/migration/**/archive/` + `ls -l ~/.opensquilla/.env` 確認 0600
- [ ] 首跑在隔離 VM / 容器，用低額度測試 key，**絕不接 production credentials**
- [ ] 安裝走「先落地再審」而非 `curl|sh` 直跑

---

## 與 OpenClaw 對比的一句話

OpenSquilla 的安全成熟度整體**明顯較高**（沙盒、fail-closed、密鑰紀律、綁定策略都是對 OpenClaw 事故的正確回應），唯獨最致命的 WS 劫持這條**沒拉開差距**——但它給了你 `auth.mode=token` 這個一鍵解，而 OpenClaw 當年是硬傷。
