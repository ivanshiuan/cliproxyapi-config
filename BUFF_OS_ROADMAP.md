# BUFF OS 導讀 — 三份 spec、3 週、$30、可隨時暫停

> 一份「指揮官用 5 分鐘讀完」的作戰地圖，不是第四份 spec。
> 詳細規格在 `specs/knowledge_schema.md` / `specs/knowledge_ingestion.md` /
> `specs/daily_brief_cron.md`，這份只負責讓你**看一眼就知道怎麼動**。

---

## TL;DR

三份 spec 串成一條鏈：**Week 1 schema → Week 2 ingestion → Week 3 自動 brief**。
每週一個 DevSwarm 任務，預算 ≤ USD $5，加總約 **$15-30** 含重跑。每週收口時都是一個獨立可暫停的存檔點——做到哪、停在哪都有用。終點不是「AI 取代你」，而是「AI 每天早上把 5 件最重要的事整理好放在你桌上」。

---

## 依賴鏈

```
                     人類資料源
       (Google Drive / Notion / 投資人 email / 工程估價單 / SOP)
                            │
                            ▼
   ┌─────────────────────────────────────────────────────┐
   │ Week 1  specs/knowledge_schema.md                   │
   │ ─────────────────────────────────────               │
   │ 3 張新表 + Alembic migration                        │
   │ knowledge_documents / knowledge_chunks /            │
   │ knowledge_queries（append-only）                    │
   │ reuse 既有 embeddings 表                            │
   └─────────────────────────────────────────────────────┘
                            │ schema 就位
                            ▼
   ┌─────────────────────────────────────────────────────┐
   │ Week 2  specs/knowledge_ingestion.md                │
   │ ─────────────────────────────────────               │
   │ ingest_source(): file / url / drive / inline        │
   │ → markitdown → redact 敏感詞 → 切 chunk             │
   │ → embed → 單一 txn 寫 DB → audit                    │
   └─────────────────────────────────────────────────────┘
                            │ 知識庫有資料
                            ▼
   ┌─────────────────────────────────────────────────────┐
   │ Week 3  specs/daily_brief_cron.md                   │
   │ ─────────────────────────────────────               │
   │ 4 個 APScheduler cron job：                         │
   │   09:00 *     today_top5    → MORNING_BRIEF.md      │
   │   18:00 *     engineering   → ENGINEERING_GAPS.md   │
   │   09:00 Mon   investor_qa   → INVESTOR_QA_PREP.md   │
   │   17:00 Fri   weekly_review → WEEKLY_REVIEW.md      │
   └─────────────────────────────────────────────────────┘
                            │
                            ▼
                你早上打開 markdown 檔就看到結果
              （不會自動發給投資人、員工、包商任何一個人）
```

---

## 一頁讀完三份 spec

### Week 1：`specs/knowledge_schema.md`

| 欄位 | 內容 |
|---|---|
| 做什麼 | 3 張新表 + 1 個 enum + 1 份 Alembic migration |
| 為什麼這個先 | 沒有 schema 就沒地方放資料 |
| AC 數 | 14 |
| 預估 DevSwarm 成本 | $3 USD（重跑一次 $6） |
| 完成後解鎖 | 可手動 `INSERT` 一份文件當測資；retrieval 端可以開始開發 |
| 你要做 | 跑 `make swarm REQ="$(cat specs/knowledge_schema.md)"`，等 20-40 分；產出綠了 `make promote TASK=knowledge_schema` 搬進來、`make full-check`、開 PR |
| 不做也行嗎 | 不行——後面兩週都靠它 |

### Week 2：`specs/knowledge_ingestion.md`

| 欄位 | 內容 |
|---|---|
| 做什麼 | 6 個檔的 ingestion service：file/url/drive/inline → markitdown → redact → chunk → embed → DB |
| 為什麼這個次之 | schema 是空盒子，這週開始往裡塞東西 |
| AC 數 | 17 |
| 預估 DevSwarm 成本 | $5 USD（複雜度高，可能要重跑） |
| 完成後解鎖 | 可以灌第一份 BP 進去並 retrieve；NotebookLM 可以開始降級為「研究實習生」（你個人用，不在 agent loop 裡） |
| 你要做 | 同上 + **準備 5-10 份起手資料**（最新 BP、3-5 份估價單、品牌定位手冊、1-2 份 SOP） |
| 不做也行嗎 | 可以暫停在 Week 1；但你會錯過 Week 3 |

### Week 3：`specs/daily_brief_cron.md`

| 欄位 | 內容 |
|---|---|
| 做什麼 | 4 個 APScheduler cron 加進 `restaurant_api/jobs/`，每個從知識庫拉資料、過 LLM、寫一份 markdown |
| 為什麼最後 | 它依賴前兩週的成品 |
| AC 數 | 17 |
| 預估 DevSwarm 成本 | $4 USD |
| 完成後解鎖 | 你每天早上 09:00、下午 18:00、週一 09:00、週五 17:00 自動有 brief 在 repo 內 |
| 你要做 | 同上 + **逐字審 spec §6 的 4 個 prompt template**（你直接看 brief 會講什麼話） |
| 不做也行嗎 | 可以——Week 2 收口後可以手動問問題、手動產 brief。Week 3 只是「自動化你的每日問題」 |

---

## 預算（USD）

| 項目 | 估計 | 備註 |
|---|---|---|
| Week 1 DevSwarm | $3-6 | spec 簡單；單次或重跑 1 次 |
| Week 2 DevSwarm | $5-10 | spec 複雜；重跑機率較高 |
| Week 3 DevSwarm | $4-8 | 中等複雜度；prompt 設計可能要 2 輪 |
| 一次性 embedding（5-10 份起手資料） | $0.50-2 | voyage-3-large @ 0.18/M tokens |
| 持續 brief LLM（4 個 cron × 30 天）| $5-15/月 | Claude Opus 4.7，含 prompt cache |
| **3 週 build 總計** | **$12-26** | 含重跑緩衝 |
| **第一個月運轉** | **+$6-17** | 開店前的量級 |

對照 CLAUDE.md「一個任務預算 USD < $5；月總額 < $50」—— 全程在預算內。

---

## 可暫停點

| 暫停在 | 你已經擁有什麼 | 損失什麼 | 何時適合停 |
|---|---|---|---|
| Week 1 收口後 | DB 有 schema、可手動塞資料測試 | 沒有自動 ingestion、沒 retrieval | 想先看 schema 跑得起來再決定 |
| Week 2 收口後 | 可以 `curl POST /knowledge/ingest`、`curl POST /knowledge/ask` 問問題（router 是後續 spec、但 service 可從 CLI 跑） | 沒有自動每日 brief | 想先親手用一陣子確定 prompt 寫得對再自動化 |
| Week 3 收口後 | 4 份 brief 自動每天產 | — | 這是 BUFF OS Phase 1 完整收口點 |

**鐵則**：每個收口點都是獨立可 PR、可 merge、可上 production 的存檔點，不是半成品。

---

## 三份 spec 的「永遠 out of scope」清單

避免日後 scope creep。下列**從來不會**進這三份 spec：

- 自動發訊息給投資人 / 員工 / 包商
- Notion / Drive / Email / LINE sink（後續 spec）
- HTTP API 即時觸發 brief
- 多 tenant 排程
- 多語輸出
- 真實「自動化收米」（需要店開門 + POS 接通 + 30 天歷史資料）

---

## 風險紅燈

| 風險 | 機率 | 應對 |
|---|---|---|
| DevSwarm 跑爛產出不對 | 中 | spec 已寫死 AC，跑爛 = 測試紅，`make promote` 不會搬，預算 hard cap | 
| Anthropic API quota / 漲價 | 低-中 | `services/llm_client` 是 Protocol，可換 backend；CLAUDE.md 已記月預算 $50 cap |
| 起手資料含敏感詞被 redact 過頭 | 中 | spec §7 的 deny-list 是常數，誤判就改常數 + 加測試 |
| Prompt template 產出「奶油網美 CP值」歪掉 | 中 | spec §11 AC-17 已有禁用詞偵測 + 寫進 `brief_runs.meta` 標記 |
| markitdown 處理某些 PDF 卡住 | 中 | spec §11 AC-13 已要求 60s timeout + rollback |
| 你開店前狀況變了（場地、估值、團隊） | 高 | 三份 spec 都不綁業務內容，只綁「資料 → 摘要 → 行動清單」的形狀 |

---

## 現在按哪個按鈕

最短路徑、最快看到自動化雛形：

```bash
# Week 1（20-40 分鐘）
make swarm REQ="$(cat specs/knowledge_schema.md)"
make promote TASK=knowledge_schema
make full-check

# 跑綠了開 Week 1 PR、按 merge

# Week 2（30-60 分鐘）
make swarm REQ="$(cat specs/knowledge_ingestion.md)"
make promote TASK=knowledge_ingestion
make full-check

# 灌第一份資料測試
.venv/bin/python -c "
import asyncio, base64, uuid
from restaurant_api.services.knowledge_ingestion import ingest_source, IngestionSource
# 把你最新 BP 的 markdown 版倒進去（如果還是 PDF，先 make to-md FILE=...）
with open('docs/BP_V8.1.md','rb') as f:
    src = IngestionSource(
        tenant_id=uuid.UUID('<your-tenant-id>'),
        scope='funding',
        inline_bytes_b64=base64.b64encode(f.read()).decode(),
        tags=['BP','2026Q2'],
    )
# ... 跑 ingest_source(src, session=...)
"

# Week 3（20-40 分鐘）
make swarm REQ="$(cat specs/daily_brief_cron.md)"
make promote TASK=daily_brief_cron
make full-check

# 跑一次 manual brief 看輸出長相
.venv/bin/python -m restaurant_api.jobs.daily_brief --kind today_top5

cat MORNING_BRIEF.md
```

跑出來 markdown 不對勁，**改 prompt template 是 1 行的事**（`specs/daily_brief_cron.md` §6 → `_brief_prompts.py` 同步），不需要重 swarm。

— end of roadmap —
