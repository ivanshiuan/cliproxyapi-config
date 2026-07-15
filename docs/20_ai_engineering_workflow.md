# 20 — AI 工程化工作流（不只 Vibe Coding）

> 靈感來源：mattpocock/skills（"real engineering, not vibe coding"）。
> 我們**沒有直接安裝**那個 repo — 它的 skill 是給通用 TypeScript 情境寫的；
> 我們把它的**四個失敗模式對策**翻譯成本專案自己的指令與 skill，跟既有工具（/spec、/swarm、/check）接成一條線。

---

## 四個失敗模式 → 本專案的武器

| AI Coding 失敗模式 | mattpocock 的解法 | 本專案的落地 |
|---|---|---|
| ① Agent 做的不是你要的 | `/grill-me`、`/grill-with-docs` | **`/grill`**（需求拷問）→ 接 `/spec` 或 Plan Mode |
| ② 名詞漂移、Agent 囉嗦 | `CONTEXT.md`、domain-modeling | **`docs/21_domain_glossary.md`**（領域詞彙表） |
| ③ 程式碼跑不起來 | `tdd`、`diagnose` | **`tdd` skill**（紅→綠→重構）、**`diagnose` skill**（假設→證據→根因）+ 既有 `/check` |
| ④ 愈改愈難維護 | `/improve-codebase-architecture` | **`/arch-review`**（架構健康審查，只診斷不動刀） |

---

## 一個中型功能的完整走法（照抄就對了）

```
① 需求      /grill 會員點數過期提醒
            → 產出需求簡報，Ivan 看過點頭
② 領域對齊   訪談冒出新名詞 → 登進 docs/21_domain_glossary.md
③ 規格      /spec points_expiry_notifier（蜂群任務）
            或 Plan Mode（動到多檔的手工實作）
④ 實作      /swarm（蜂群跑）或 手工 + tdd skill（紅→綠→重構）
⑤ 卡關      diagnose skill（先重現、找根因、才動刀）
⑥ 驗證      /check（ruff + pyright + pytest + alembic + smoke 全綠）
⑦ 審查      /code-review（diff 抓 bug）；累積幾個功能後跑一次 /arch-review
⑧ 交付      開 PR（一個任務一個 PR，CLAUDE.md PR 工作流）
```

**小改動**（一個檔、一眼看得完）可以跳過 ①②③，直接 tdd → /check → PR。
**問答查資料**不進這條線，直接問。

---

## 各武器一句話定位（避免拿錯工具）

| 指令/skill | 用在 | 不要用在 |
|---|---|---|
| `/grill` | 動工**前**把需求問清楚 | 需求已經寫成 spec 的東西 |
| `/spec` | 產 DevSwarm 任務簡報 | 多檔手工大改（用 Plan Mode） |
| `tdd` | 寫新邏輯、修 bug 時的節奏 | 純文件/設定改動 |
| `diagnose` | 原因不明的錯 | 已知根因的直修 |
| `/check` | 全綠門檻，commit 前必跑 | — |
| `/code-review` | 審**這次 diff** | 看整體結構（用 /arch-review） |
| `/arch-review` | 定期看**整體腐化趨勢** | 審單次 diff |

---

## 為什麼不直接裝 mattpocock/skills？

1. 它的 tdd/diagnose 假設 TypeScript + vitest；我們是 Python + 自家 conftest SAVEPOINT 機制，照裝會教 Agent 做錯事。
2. 我們已有 `/spec`→`/swarm`→`promote` 這條蜂群產線，`/to-spec`、`/to-tickets` 跟它重疊，兩套並存會讓 Agent 選擇困難。
3. 它的價值在**方法論**（需求對齊、共享語言、回饋循環、防腐化），方法論已全數落地，且每一條都綁了本專案的鐵律（Decimal、ledger、DI commit、seed scope）。

上游 repo 之後出了值得抄的新 skill → 用 `digest` skill 建知識卡，評估後再翻譯進來。
