# 知識庫（帝國第二大腦）

> 你看到的好內容 → AI 提煉成「知識卡」→ 存這裡 → 之後隨時提取來用。
> 目的：把散在腦裡、Notion、影片裡的洞見，變成**版控、可搜尋、AI 能原生讀寫**的資產。

## 怎麼用（日常）

丟連結或內容給 AI，說「**建檔 / digest 這個**」，AI 會：
1. 抓原文（公開頁用 WebFetch；要登入／反爬／社群平台用 BrowserAct）。
2. 提煉成 `_TEMPLATE.md` 的格式。
3. 存成 `docs/knowledge/YYYY-MM-DD-<slug>.md`。

也可以說「**幫我把最近 5 篇整合成一份 X 主題的洞見**」，AI 會跨卡片提取。

## 檔名規則
`YYYY-MM-DD-<簡短英文或拼音 slug>.md`，例：`2026-07-08-xiaohongshu-membership-loop.md`

## 欄位意義（重點是 `applies_to` 和「So what」）
知識卡的價值不在「摘要」，在**「這對帝國哪個環節有用」**。填 `applies_to` + 「可用在帝國哪個環節」這兩欄，未來 AI 才能在做該環節決策時自動把相關卡片撈出來。

## 標籤慣例（先小後大，用到再加）
`行銷 / 會員 / 定價 / 供應鏈 / 內場KDS / 門市營運 / 品牌 / 財務 / 競品`

## 之後想升級成「格局圖」
這些 markdown 可直接放進 **Obsidian vault** → 用 Graph view 看關係網、用 Canvas 排九宮格／世界格局圖。檔案格式不用改，Obsidian 原生吃 markdown + `[[雙向連結]]`。
