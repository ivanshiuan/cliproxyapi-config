# 負責任揭露草稿 — OpenSquilla CSWSH → 主機 RCE

> 狀態：**草稿，尚未送出。** 送出前請 Ivan 過目並決定是否投遞。
> 投遞管道：opensquilla/opensquilla 的 **GitHub Private Vulnerability Reporting**
> （Repo → Security → Report a vulnerability）。依該專案 `SECURITY.md`，
> **不要開公開 issue 附帶 exploit 細節**；若私密回報未開放，先開一個最小公開 issue
> 詢問安全聯絡管道，不含技術細節。

---

## 為什麼要揭露

- 這是預設組態即可觸發、可達主機 RCE 的 critical，影響所有跑預設設定的使用者。
- 我們打算 fork 這個專案當內核；上游修掉對我們也有利（減少 rebase 分歧、我們的 fork 站在更安全的底座上）。
- 「負責任揭露 + 提供 patch」本身就是可信度資產，呼應融合佈局裡「可信版」的定位。

---

## 揭露內文（可直接貼進 private report，送出前替換佔位）

**Title**: Cross-site WebSocket hijacking in default `auth.mode=none` grants owner+admin, leading to sandbox disable and host RCE

**Affected version / commit**: `main` @ `7f72a32`（0.5.0 Preview 1 線）

**Summary**

In the default single-machine deployment (`auth.mode=none`, gateway bound to
`127.0.0.1:18791`), the WebSocket handshake performs no `Origin` validation.
Any web page the user visits can open `ws://127.0.0.1:18791/ws` and, purely
from loopback peer provenance, be granted `CLI_DEFAULT_OPERATOR_SCOPES`
(owner + admin). From there the connection can disable the sandbox and reach
arbitrary command execution on the host.

**Impact**

- Drive-by: victim only needs the gateway running (default) and to open a
  malicious page in a browser on the same machine.
- Capabilities obtained: read all sessions/memory/chat; change `llm.base_url`
  to exfiltrate LLM traffic; install persistent skills/cron; **disable the
  sandbox and execute arbitrary host commands** (host RCE).
- No second human-approval gate blocks it — the owner principal auto-approves.

**Root cause (code pointers, commit `7f72a32`)**

- `gateway/websocket.py` `handle_ws_connection`: `ws.accept()` is called with
  no Origin check; the `connect.challenge` nonce is sent but never verified.
- `gateway/middleware.py` `AuthMiddleware.dispatch`: WS upgrades are passed
  through unconditionally ("WS handles own auth").
- `gateway/auth.py` `OpenScopeResolver`: in no-auth mode, grants
  `CLI_DEFAULT_OPERATOR_SCOPES` from `is_loopback_bind(host) and
  is_loopback_address(peer_ip)` alone — a browser on the same host satisfies
  both.
- `sandbox/run_mode_policy.py`: owner is permitted `FULL` run mode; combined
  with `rpc_sandbox.py` `sandbox.run_context.set` (write scope) or
  `/api/elevated-mode` (owner only), the sandbox can be turned off.

**Reproduction (placeholders — no real credentials needed)**

1. Run the gateway with defaults: `opensquilla gateway run` (mode=none, bound
   to `127.0.0.1:18791`).
2. From any origin, in a browser on the same host, run JS:
   `const ws = new WebSocket("ws://127.0.0.1:18791/ws")`.
3. Send a connect frame with an empty `auth` object and `role: "operator"`.
4. Observe the `HelloOk` response granting owner/admin scopes without any
   secret. (Escalation to host RCE via run-mode switch + shell tool follows,
   omitted here.)

**Suggested fix**

Enforce an `Origin` allowlist before `ws.accept()`: allow requests with no
`Origin` header (native clients) and requests whose `Origin` is the gateway's
own loopback origin or an operator-configured `control_ui.allowed_origins`
entry; reject all others. A patch against `7f72a32` is available and can be
shared privately on request. Verifying the `connect.challenge` nonce does not
defend this (the attacker's JS can read the challenge frame), so the Origin
check is the load-bearing control. Consider also tightening the default CORS
config (`allow_origins=["*"] + allow_credentials=True`).

**Disclosure terms**

Happy to coordinate. Requesting standard embargo until a fixed release or
mitigation is available; will not publish technical details before then.

---

## 送出前檢查清單

- [ ] Ivan 確認要投遞（vs 只留 fork 內部修）
- [ ] 確認 opensquilla 有開 GitHub Private Vulnerability Reporting；若無，先送最小公開 issue 問聯絡管道
- [ ] 內文佔位（版本/commit）與實際一致
- [ ] 不在公開處貼 exploit 細節、不含任何真實憑證
- [ ] 決定是否主動附上 patch（可作為善意 + 可信度資產）
