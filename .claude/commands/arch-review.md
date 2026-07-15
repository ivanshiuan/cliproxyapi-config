---
description: 架構健康審查（improve-codebase-architecture）— 掃描腐化徵兆，輸出排序過的改善清單，不動手改
argument-hint: [可選：限定範圍，如 restaurant_api/services 或 devswarm]
allowed-tools: Read, Glob, Grep, Bash
---

你是**架構審查官**。範圍：`$ARGUMENTS`（空白 = 全 repo）。
任務是找出「程式碼愈改愈難維護」的腐化徵兆，輸出**排序過的改善清單**——只診斷、不動刀。

> 這是 mattpocock/skills 的 `/improve-codebase-architecture` 在本專案的落地版。
> 與 `/code-review`（審 diff 抓 bug）、`/simplify`（改品質）不同：這個看的是**整體結構趨勢**。

## 審查清單（照本專案的不變法則逐項掃）

### 分層紀律
- service 裡有沒有偷 `session.commit()`？（commit 只能在 `api/deps.py` DI 層）
- router 裡有沒有業務邏輯漏出來？（router 應薄，邏輯進 services/）
- 有沒有 raw `HTTPException` 繞過 `api/errors.py` 的 DomainError 體系？
- 有沒有直接 INSERT `AuditLog` 繞過 `audit_service.audit()`？

### 型別與金錢紀律
- 有沒有 float 碰錢？（grep `float` 在 models/schemas/services 附近）
- ORM money 欄位有沒有不走 `Money = Numeric(14, 4)` 別名的？
- 新表有沒有漏 `tenant_id` / `created_at` / `updated_at`？

### 重複與發散
- schemas/ 之間有沒有複製貼上長大的重複 Pydantic 模型？
- 同一個領域概念有沒有兩套命名？（對照 `docs/21_domain_glossary.md`）
- tests/ 裡有沒有繞過 conftest fixture 自己建 session 的孤兒測試？

### 尺寸警戒
- 單檔 > 500 行的 service/router 列出來
- 單函式 > 80 行的列出來

## 產出格式

```markdown
# 架構審查報告 <日期> — 範圍：<範圍>

## 總評（一段話：健康 / 有隱患 / 需要停下來還債）

## 發現（按severity排序）
| # | 嚴重度 | 位置 | 問題 | 建議 | 預估工作量 |

## 建議的還債順序（最多 3 項，說明為什麼是這 3 項）

## 不建議現在動的（發現了但先放著的，說明理由）
```

## 鐵律

- **不改任何檔案**。要修就等使用者看完報告點頭，再開新任務。
- 每個發現都要附 `檔案:行號`，不要泛泛而談。
- 嚴重度定義：🔴 違反不變法則（錢/ledger/分層）｜🟡 重複或發散｜🟢 尺寸或風格。
