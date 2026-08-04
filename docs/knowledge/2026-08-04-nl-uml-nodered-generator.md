---
source_type: 其他        # 使用者上傳截圖 ×2（App 產品拆解）
source_url: 無外部連結 — 截圖 App 為「流光 流程動態生成器」(FLOW · 動態流程)，IMG_4649 / IMG_4650
captured_at: 2026-08-04
tags: [PlantUML, UML, Node-RED, 低程式碼, 流程自動化, NL轉程式, 產品拆解, 接案變現]
applies_to: [系統與AI]
status: inbox
---

# 一句需求 → UML → 流程圖 → Node-RED：可雙向編輯的流程生成器，怎麼做到的

## 一句話重點
整套產品只有一個核心祕密：**流程的「唯一真相」是一份中間資料模型（IR/AST），文字、圖、動畫、Node-RED 全是它的投影**——每個投影都能編輯並寫回，所以才「什麼都能轉、什麼都能改」；畫圖不值錢，**轉成可執行流程（Node-RED）才是企業掏錢的點**。

## 核心重點（5 條）

1. **圖不是圖，是資料。** 畫面上「6 節點 · 14 連線」「位置由結構決定」洩露了實作：內部維護一份流程 AST（參與者、訊息、alt 分支、迴圈），PlantUML 文字 ↔ AST 有 parser/printer 一對，SVG、動畫、flows.json 全是 AST 的純函數投影。改文字→重新 parse；點圖編輯→改 AST→重新產文字。雙向同步不是魔法，是「只有一份真相」。

2. **犧牲自由排版，換取雙向編輯。** 時序圖排版天生是決定式的：參與者按宣告順序排 X 軸、訊息按行序排 Y 軸。不給拖曳（「位置由結構決定」「自動排版」）＝不用存座標＝沒有 layout 同步地獄。這是「可編輯」成本低的關鍵取捨。

3. **雙渲染路徑。** 「本機繪製」＝自寫 PlantUML **子集** parser + SVG 渲染，每個圖元帶 data-id 才能點選（已選:系統 → 編輯/連線/刪除）；「PlantUML 伺服器」＝官方 deflate+base64 編碼 URL（「連結」按鈕同原理），支援完整語法但**圖會外流公網**——企業版要自架 plantuml.jar，這本身就是賣點。

4. **「免 API」的 NL→UML 是範本庫。** 「快餐店點餐系統」產出的是教科書級標準時序圖——極可能是關鍵字→領域範本（點餐/審批/電商各一套骨架）+ 槽位填充。要做得比它好：換成一次 LLM call（system prompt 限定「只輸出 PlantUML 時序圖子集」+ few-shot），品質立刻超車，離線需求才 fallback 範本。

5. **Node-RED 只是 JSON 映射。** flows.json 就是 `{id, type, x, y, wires}` 陣列：訊息→節點、箭頭→wires、alt→switch 節點雙出口、迴圈→loop。~300 行 mapper 就能雙向轉（畫面也有「由 Node-RED 流程產生 UML」反向鍵）。視覺編輯聚焦時序圖，其他圖種（類別/甘特/WBS/SALT）走文字 snippet 按鈕，成本控制得很聰明。

## 架構蒸餾（一張圖）

```
一句需求 ──① NL→UML(範本或LLM)──▶ PlantUML 文字
                                      ▲│
                          ② parser/printer（雙向）
                                      │▼
                            ★ 流程資料模型 IR ★  ←—— 唯一真相
                                      │ ③ 投影（純函數、決定式排版）
        ┌───────────────┬─────────────┼───────────────┐
        ▼               ▼             ▼               ▼
  SVG（帶 data-id）   流動動畫      Node-RED        SVG/PNG/.puml
  ④ 點選編輯寫回 IR  （訊息依序播） flows.json ⑤雙向  /分享連結（編碼URL）
```

三句話帶走：**(1) 所有視圖都是同一份資料的投影；(2) 決定式排版讓雙向編輯變便宜；(3) 最後一哩「轉可執行」才是變現點。**

## 可用在帝國哪個環節（So what）

- **餐飲 SOP 自動化（最直接）**：一句「每天 21:30 沒收到盤點回報就 LINE 通知店長」→ 流程圖給 Ivan 確認 → 轉成排程+通知流程。RestSwarm 已有 `jobs/`（APScheduler）+ `integrations/line/`——**這產品的執行後端我們已經有一半**，缺的只是「需求→流程 IR→Job 定義」的前段。
- **DevSwarm spec 配圖**：`specs/*.md` 自動長出時序圖（spec 文字→LLM→PlantUML→SVG 進 PR 描述），指揮官看圖審 spec、不看程式碼——完全貼合現有 PR 工作流。
- **接案變現方向（改一下賺摳摳）**：把輸出目標從 Node-RED 換成 **BPMN 2.0 XML（Camunda/Flowable 可直接匯入）** 或 **n8n JSON**，就是真正的企業內部需求：需求訪談當場出圖確認 → 直接落地成自動化草稿。企業版三件套＝自架渲染（資安）+ 權限 + 版本歷史。
- **技術成本低**：純前端 2–3k LOC 有 MVP（parser ~500、排版渲染 ~500、編輯層 ~500、動畫 ~150、Node-RED mapper ~300），零後端，Cloudflare Pages 可部署——deploy-sid 管線現成。

## 行動項（Next action）
- [ ] 若要做 MVP：第一週只做核心「時序圖子集 parser + 決定式排版 + data-id SVG 編輯」；NL→UML 直接用 Claude API 一個 prompt，**不要**自寫範本庫。
- [ ] 評估把「餐飲 SOP 一句話→流程確認→LINE 排程通知」做成第一個垂直 demo（後端 RestSwarm 現成，最短變現路徑）。

## 原文摘錄 / 截圖證據

- App 抬頭：「FLOW · 動態流程／流光 流程動態生成器」；左欄「**由敘述產生 UML(免 API)**」「AI 生成 UML(自然語言 → PLANTUML)」，輸入框內容僅一句：「快餐店點餐系統」，圖種下拉「時序圖」。
- 產出 34 行 PlantUML：`actor 客戶`、`participant 收銀員/系統/廚房/付款系統/訂單管理`、`客戶 -> 收銀員 : 點選餐點`、`alt 付款成功 … else 付款失敗 … end`、`activate/deactivate 廚房`、`note right of 廚房 : 製作中...`——中文識別字直接當 token，parser 以關鍵字+箭頭切分。
- 工具列：「本機繪製｜PlantUML 伺服器」切換、「更新」「播放流動」「視覺編輯/完成編輯」「整理」「SVG/PNG/.puml/連結/大圖」「轉流光動畫」「轉 Node-RED」。
- 視覺編輯模式：新增節點「參與者/角色/資料庫/佇列」（＝PlantUML `actor/participant/database/queue`）；關聯型別「同步 `->`／回傳 `-->`／非同步 `->>`」（原生箭頭語法直出）；「已選:系統」浮出「編輯/連線/刪除」；「自動排版 · 6 節點 · 14 連線」「點圖上元件選取 · 雙擊編輯內容 · Delete 刪除 · **位置由結構決定**」。
- 互相轉換區：「由流光流程圖產生 UML」「**由 Node-RED 流程產生 UML**」（雙向）、「清空重新編寫」；註記「編輯器即時重繪 · Ctrl+Enter 立即更新 · 可匯出 SVG/PNG/.puml · 『PlantUML 伺服器』模式支援完整官方語法」。
- 其他圖種 snippet 按鈕：類別/繼承/狀態/使用案例/元件/實體/關聯/甘特/里程碑/WBS/容器/輸入框/按鈕（後兩者＝SALT UI mockup）。
