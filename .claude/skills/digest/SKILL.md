---
name: digest
description: "把一段內容或一條連結提煉成『知識卡』並歸檔進 docs/knowledge/。當使用者說：建檔、digest、整理這篇、幫我消化、把這個影片/貼文/文章整理起來、存進知識庫、把 XX 學起來、這個對我們有用記下來，或丟來一條 YouTube / 文章 / 小紅書 / 抖音 / TikTok / Facebook 連結並要你歸納時觸發。公開頁用 WebFetch 抓；需要登入或反爬或社群平台用 browser-act 抓。輸出一張 docs/knowledge/YYYY-MM-DD-<slug>.md 知識卡，並回報存到哪。"
allowed-tools: Read, Write, WebFetch, Bash
---

# digest — 內容 → 知識卡

把使用者丟來的**連結或內容**變成一張結構化知識卡，存進 `docs/knowledge/`。

## 流程

1. **取得內容**
   - 使用者直接貼了文字 → 直接用。
   - 公開網頁 / YouTube / 文章連結 → 用 **WebFetch**（YouTube 抓標題+逐字稿+說明；文章抓正文）。
   - 需要登入、有反爬、或是小紅書/抖音/TikTok/Facebook 這類平台 → 用 **browser-act**（先叫 browser-act skill、跑 `get-skills core`）。若 browser-act 尚未能連（網路白名單未開），**明講卡在哪**，並請使用者改貼「內容文字」或先開白名單，不要瞎猜內容。

2. **提煉**：讀 `docs/knowledge/_TEMPLATE.md`，照它的欄位填。
   - 重點在 `applies_to` 與「可用在帝國哪個環節（So what）」——這是知識卡的價值，不要只做摘要。
   - 不確定歸到哪個環節時，用 AskUserQuestion 給 2–3 個選項，不要亂填。

3. **歸檔**：存成 `docs/knowledge/YYYY-MM-DD-<slug>.md`（日期用今天；slug 用簡短英文/拼音）。

3b. **登記進 MOC**：把 `[[<檔名不含.md>]]` 依 `applies_to` 加到 `docs/knowledge/00_MOC.md` 對應環節的小節下（一卡可登多個環節）。這步讓 Obsidian 的 Graph/Canvas 格局圖自動長出連結，不可省略。

4. **回報**：一句話講「存到哪 + 一句話重點 + 我建議的行動項」。若有明顯可立即執行的行動，問使用者要不要接著做。

## 原則
- **金錢/數字不亂填**：抓到的價格、數據只是參考，要入帳仍走 restaurant_api 結構化驗證（呼應 CLAUDE.md 金錢法則）。
- **一次一張卡**，內容太雜就拆多張、彼此用連結串。
- **來源一定留連結**，方便日後查證。
- 大規模爬社群平台有封號/ToS 風險——**只做「單點消化使用者指定的內容」，不做無腦全站爬**。
