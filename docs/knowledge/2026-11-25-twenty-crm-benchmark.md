---
source_type: article
source_url: https://docs.twenty.com/user-guide/data-model/overview
captured_at: 2026-11-25
tags: [競品, CRM, 時間軸, 備註, 任務, 標籤, 關係經營, 開源]
applies_to: [競品情報, 會員成長, 系統與AI]
status: applied
---

# Twenty CRM — 開源 Salesforce 替代：關係經營的「客戶時間軸」範本

## 一句話重點
Twenty 的核心不是欄位多，是「打開一個客戶就看到全部來往」的統一時間軸 + 備註 + 任務 + 標籤；我們把這層補進 CRM，並在時間軸裡多塞 Twenty 沒有的「訂單、集點、儲值、推薦」——變成懂餐飲的關係經營 CRM。

## 核心重點（3–5 條）
- 標準物件 People / Companies / Opportunities + Notes / Tasks，支援多對多關聯。
- **Activity 時間軸**是殺手鐧：溝通、備註、任務串成一條。
- 自訂物件與內建物件同等（API、視圖、權限、工作流），AGPL v3、GraphQL + REST。
- 它是 B2B 銷售 CRM，**完全沒有忠誠度/集點/儲值概念** —— 這是我們的護城河。

## 可用在帝國哪個環節（So what）
- **會員成長**：店長能派跟進任務（客訴回訪、生日禮）、貼標籤（VIP/素食/過敏），會員系統從「集點錢包」升級成關係經營。
- **系統**：已落地 `/crm/*` 5 表 + 時間軸 API（docs/14，migration `c4e9b2d71a05`）。
- **行銷敘事**：「唯一把消費 + 忠誠度 + 關係經營放在同一條客戶時間軸上的餐飲系統」。

## 行動項（Next action）
- [x] 補 notes / tasks / tags / interactions / timeline（本次）
- [ ] Phase 2：工作流自動化（VIP 30 天未到店 → 自動建任務，接 `jobs/`）
- [ ] Phase 2：LINE 訊息自動寫 interactions（接 LINE webhook）
- [ ] Phase 2：用標籤分眾廣播（接 `/membership/segments`）

## 原文摘錄 / 逐字稿重點
> "Companies, People, and Opportunities" are the standard objects, plus Tasks and Notes.（docs.twenty.com）
> Custom objects get the same treatment as built-in ones, including API endpoints, views, permissions, and workflow triggers.
> Licensed under AGPL v3, with GraphQL and REST APIs.

完整對照：`docs/14_twenty_crm_enhancement.md`
