# OpenSquilla 情報卷宗

> 對 [opensquilla/opensquilla](https://github.com/opensquilla/opensquilla) 的深度分析、安全審計,
> 以及「以它為內核、融合 OpenClaw 生態與 Hermes 自我進化迴路」的佈局計畫。
>
> 產出時間：2026-07-05 · 審計對象 commit：`7f72a32`（main）

## 目錄

| 檔案 | 內容 | 一句話結論 |
|---|---|---|
| [`00_deep_analysis.md`](00_deep_analysis.md) | 定位 / 架構 / 成熟度 + vs OpenClaw vs Hermes 三方比較 | 成本效率最強、生態最弱、觀察名單等級 |
| [`01_security_audit.md`](01_security_audit.md) | 四維平行審計 + 對抗性覆核 | 沙盒扎實，但預設組態有一個 **CSWSH→主機 RCE** critical |
| [`02_fusion_plan.md`](02_fusion_plan.md) | 核/器官/工廠/通路四層佈局 + 硬化 backlog | 有條件 GO；WS Origin 加固是第一戰 |
| [`03_responsible_disclosure.md`](03_responsible_disclosure.md) | 負責任揭露草稿 | 走 GitHub private vulnerability reporting |
| [`../../patches/opensquilla-ws-origin-validation.patch`](../../patches/opensquilla-ws-origin-validation.patch) | 硬化 backlog #1 的可套用 patch | WS 握手加 Origin allowlist + nonce 真驗證 |

## 三十秒摘要

- **是什麼**：2026-05 公開的 token-efficient 微核心 AI agent，靈魂是本地 ML 路由器 SquillaRouter，同等智能號稱約 1/9 成本。受 OpenClaw 啟發，內建 `migrate` 可從 OpenClaw/Hermes 搬家。
- **強在哪**：真隔離沙盒（Bubblewrap/Seatbelt、fail-closed）、成本效率、密鑰紀律、繁中友善。
- **弱在哪**：Preview 階段、5.4k stars（vs OpenClaw 382k / Hermes 209k）、生態小、團隊匿名。
- **一個致命洞**：預設 `auth.mode=none` + WS 握手零 Origin 驗證 → 任一惡意網頁可劫持本地 agent、關掉沙盒、在你電腦上 RCE。**設 `auth.mode=token` 可完全堵死。**
- **佈局判斷**：**有條件 GO**。可 fork 當內核、隔離環境開發；接真 key 前先關洞。那個 critical 恰好是「我 fork 的版本修掉原版一個能被任何網頁 RCE 的漏洞」——這本身就是一個能打的定位故事。
