---
source_type: post        # Ivan 貼文（引用工商時報新聞）
source_url: https://vibeic.ai
captured_at: 2026-07-23
tags: [AI, IC設計, EDA, 開源, Kimi, 產業趨勢, AI-native]
applies_to: [系統與AI]
status: inbox
---

# AI 開始吃掉 IC 設計：Kimi K3 48 小時做晶片、EDA 龍頭應聲跌，Vibe-IC 開源補上開放版本

## 一句話重點
「只要是邏輯性的東西，基本上都有機會被 AI 取代與超越」——軟體之後下一個是 IC 設計；Kimi K3 用純開源工具 48 小時端到端做出功能性晶片，而作者（Ivan 引用的貼文）四個月做出開源 AI-native IC 設計流程 Vibe-IC 並於 2026/7/20 開源。

## 核心重點（3–5 條）
- **事件**：Moonshot 發表 Kimi K3，號稱只用開源工具、48 小時內獨立完成一顆功能性晶片設計、完全繞過商用 EDA；據工商時報，Cadence、Synopsys 於 7/17 分別下跌 9.47% 與 7.85%。
- **論點**：AI 先吃軟體是因為「純文字＋明確對錯＋海量語料」；IC 設計同樣高度邏輯化，被 AI 推進只是早晚問題。
- **Vibe-IC 成果（可驗證）**：VerilogEval-v2 / VerilogEval-Human 皆 98.08%（153/156, single-shot）；RTLLM 98%（49/50）；端到端跑通 7 顆真實 IC（spec → GDSII，含 RISC-V SERV SoC），4/4 顆開放 IC 最嚴格模式 PASS_WITH_WAIVERS。
- **關鍵差異**：不是「直接拿開源 EDA 來用」，而是 fork 十幾個開源工具（KLayout、Yosys、OpenROAD、OpenSTA、Magic、ngspice…）自己修 bug、補功能，每個 fork tracked to upstream、公開可查——整條流程的每個工具都掌握在自己手裡。
- **誠實邊界**：尚不能直接 tape-out、未經正式專家審查、未在先進製程與商業流程正面比過；複雜 SoC 仍難。數據公開於 vibeic.ai/evaluation。

## 可用在帝國哪個環節（So what）
- **方法論同構**：Vibe-IC 的路數（AI 主導 + fork 整條開源工具鏈自己掌握 + benchmark 盲測 + 誠實列出做不到的地方）正是 DevSwarm 的放大版——「一套完整的設計文件 → 產出物」對應我們的「spec → router + tests」。可借鑑：把 specs/ 的驗收標準做成可盲測的 benchmark、對依賴工具保持可 fork 可掌握。
- **戰略驗證**：「邏輯性工作終將被 AI 吃下」支撐帝國押注 AI-native 營運系統（RestSwarm/DevSwarm）的第一性原理；餐飲後台的對帳、排班、損益等邏輯性流程都在同一條浪上。
- **傳播範本**：這篇貼文本身是好的開源發布文案結構——事件鉤子 → 個人判斷 → 可驗證成果 → 誠實邊界 → 邀請參與，日後 RestSwarm 對外發布可套用。

## 行動項（Next action）
- [ ] 讀 vibeic.ai 兩篇推論文（第一性原理、AI-native 端到端賭注），評估可否轉化成 DevSwarm 的 spec/benchmark 強化方向
- [ ] 參考其「fork + tracked to upstream」策略，盤點 DevSwarm/RestSwarm 的關鍵依賴是否需要同等掌握度

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
