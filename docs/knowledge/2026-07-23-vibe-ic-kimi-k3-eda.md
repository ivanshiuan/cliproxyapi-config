---
source_type: post        # Ivan 貼文（引用工商時報新聞）
source_url: https://vibeic.ai
captured_at: 2026-07-23
tags: [AI, IC設計, EDA, 開源, Kimi, 產業趨勢, AI-native, 方法論]
applies_to: [系統與AI, 品牌]
status: reviewed
---

# AI 開始吃掉 IC 設計：Kimi K3 48 小時做晶片、EDA 龍頭應聲跌，Vibe-IC 開源補上開放版本

## 一句話重點
「只要是邏輯性的東西，基本上都有機會被 AI 取代與超越」——軟體之後下一個是 IC 設計；Kimi K3 用純開源工具 48 小時端到端做出功能性晶片，而 Vibe-IC 作者四個月做出開源 AI-native IC 設計流程並於 2026/7/20 開源。**對帝國的價值不在晶片，在方法論。**

## 核心重點（3–5 條）
- **事件**：Moonshot 發表 Kimi K3，號稱只用開源工具、48 小時內獨立完成一顆功能性晶片設計、完全繞過商用 EDA；據工商時報，Cadence、Synopsys 於 7/17 分別下跌 9.47% 與 7.85%。
- **論點**：AI 先吃軟體是因為「純文字＋明確對錯＋海量語料」；IC 設計同樣高度邏輯化，被 AI 推進只是早晚問題。
- **Vibe-IC 成果（可驗證）**：VerilogEval-v2 / VerilogEval-Human 皆 98.08%（153/156, single-shot）；RTLLM 98%（49/50）；端到端跑通 7 顆真實 IC（spec → GDSII，含 RISC-V SERV SoC），4/4 顆開放 IC 最嚴格模式 PASS_WITH_WAIVERS。
- **關鍵差異**：不是「直接拿開源 EDA 來用」，而是 fork 十幾個開源工具（KLayout、Yosys、OpenROAD、OpenSTA、Magic、ngspice…）自己修 bug、補功能，每個 fork tracked to upstream、公開可查——整條流程的每個工具都掌握在自己手裡。
- **誠實邊界**：尚不能直接 tape-out、未經正式專家審查、未在先進製程與商業流程正面比過；複雜 SoC 仍難。數據公開於 vibeic.ai/evaluation。

## 可用在帝國哪個環節（So what）— 完整歸納

> 先講結論：**對餐飲業務本身零直接幫助**（我們不做晶片）。價值全在間接層，
> 按「可行動程度」由高到低分三層。不改變本週優先級——Phase 1 餐飲後端仍是主線。

### 第一層：方法論可以直接抄（DevSwarm）

Vibe-IC 與 DevSwarm 是同一個形狀：**「完整 spec → AI 端到端產出 → 盲測驗證 → 誠實列邊界」**。
它證明這條路在比軟體難得多的領域（IC 設計）也走得通。四件可抄的事：

1. **把驗收標準做成可盲測的 benchmark**。
   - 現況：DevSwarm 產出「pytest 綠了就算過」，是自己出題自己改考卷。
   - 抄法：仿 VerilogEval 的 runner 盲測——spec 的 AC 由獨立 runner 執行、
     產出公開成績單（N/M 通過、single-shot 或幾次迭代）。
   - 用途：日後對外證明 DevSwarm 能力（募資簡報、找合作、徵才）時，
     「盲測 98% 通過」比 demo 影片有說服力一個量級。
2. **spec 品質是成敗上限**。Vibe-IC 的起點是「一套完整的設計文件」——
   呼應我們既有法則「AC ≥ 10、out-of-scope 列清」。它的成功再次驗證：
   蜂群輸出品質的天花板是 spec 寫作品質，投資 spec-writer agent 是對的。
3. **「fork + tracked to upstream」的依賴掌握策略**。
   - 它把整條流程的每個工具 fork 到自己手裡、修 bug 補功能、但保持與上游同步。
   - 抄法：盤點 DevSwarm/RestSwarm 的關鍵依賴（LINE SDK、APScheduler、
     LangGraph、markitdown…），分級：哪些「上游斷了會卡死營運」→ 需要
     fork 或至少 vendor lock 版本；哪些可隨時替換 → 不用管。
4. **誠實列出「還做不到的地方」是資產不是弱點**。它公開列了輸給商用 EDA 之處，
   反而增加可信度。抄法：RestSwarm 對外文件（給投資人/合作店家）也放
   「目前做不到」清單——在台灣餐飲 SaaS 圈這種誠實罕見，本身就是差異化。

### 第二層：對外發布的文案範本（品牌）

那篇貼文本身是一個高完成度的開源發布文案結構，日後 RestSwarm 對外發布直接套：

| 段落 | 作用 | 對應我們 |
|---|---|---|
| 事件鉤子（Kimi K3 + 股價） | 借勢頭條建立相關性 | 借 AI/餐飲業新聞開場 |
| 個人判斷（邏輯性工作終將被吃） | 立論點、給記憶點 | 「餐飲後台是邏輯性工作」 |
| 可驗證成果（benchmark 數字 + 連結） | 建立可信度 | 盲測成績單、真實門市數據 |
| 誠實邊界（還做不到什麼） | 反向增信 | 「目前做不到」清單 |
| 邀請參與（留 email、貢獻） | 轉化 | 試點店家報名、開發者社群 |

另兩個小技巧：教學影片定位成「學習過程的副產品」降低製作壓力；
「這篇也是與 Claude 協作完成」的透明標註可以學。

### 第三層：戰略信心佐證（不產生行動）

「邏輯性工作終將被 AI 吃下」本來就是帝國押 AI-native（DevSwarm/RestSwarm）的
第一性原理前提；這則新聞多一個資料點——對帳、排班、損益、BOM 扣料全是邏輯性
流程，都在同一條浪上。**只是佐證，不改變任何已做的決策。**

## 行動項（Next action）

- [ ] **（低優先）** 讀 vibeic.ai 兩篇推論文（第一性原理、AI-native 端到端賭注），評估「盲測 benchmark」轉化成 DevSwarm spec 驗收強化的具體做法
- [ ] **（低優先）** 盤點 DevSwarm/RestSwarm 關鍵依賴，分級「斷了會卡死 / 可替換」，決定是否需要 fork 或 vendor
- [ ] **（待觸發）** RestSwarm 首次對外發布時，回來套用第二層的文案結構表

## 原文摘錄 / 逐字稿重點
> 「只要是邏輯性的東西，基本上都有機會被 AI 取代與超越。」
> 「它號稱只用開源工具、在 48 小時內就獨立完成一顆功能性晶片的設計，完全繞過商用 EDA……Cadence、Synopsys 於 7/17 分別下跌 9.47% 與 7.85%。」
> 「我把整條流程用到的開源工具 fork 出來……自己修 bug、自己補功能……每個 fork 都 tracked to upstream、公開可查。」
> 「這是一場賭注、一次探索，目前還不是能直接 tape-out、能取代商業 EDA 的成品。」

**相關連結**：
- 平台：https://vibeic.ai ｜ 數據：https://vibeic.ai/evaluation ｜ 部落格（12 篇）：https://vibeic.ai/blog
- 推論文：[第一性原理](https://vibeic.ai/blog/02-first-principles-logic-and-ai-zh.html)、[這場賭注](https://vibeic.ai/blog/01-ai-native-end-to-end-bet-zh.html)
- 教學影片：[中文 YouTube](https://www.youtube.com/playlist?list=PLfprap48eFX8) ｜ [English Vimeo](https://vimeo.com/showcase/12332372)
- 新聞來源：工商時報（2026/7/17 Cadence/Synopsys 股價報導）
