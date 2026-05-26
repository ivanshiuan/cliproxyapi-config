# 01 — Tech Stack Recommendation（餐飲 SaaS 技術選型）

**Status:** Decided. This document fixes the stack for the **future restaurant SaaS** (RestSwarm application layer). It does **not** govern DevSwarm itself, which is intentionally a thin Python/LangGraph harness.
**Scope:** Backend, frontend, database, cache, infra, observability, payments, i18n.
**SSOT alignment:** [`./00_vision.md`](./00_vision.md) — 七大模組、雙層架構、Phase 0–5.
**Companion docs:** [`./02_devswarm_architecture.md`](./02_devswarm_architecture.md), [`./03_roadmap.md`](./03_roadmap.md), [`./04_data_schema.md`](./04_data_schema.md).

---

## 1. Decision drivers（為什麼這套，不是那套）

指揮官的真實狀況決定了所有技術取捨。這不是「最潮的就最好」，而是「能讓 AI 蜂群最快端出可商用單店系統」的最佳組合。

| # | Driver | Description | 影響到的選型 |
|---|---|---|---|
| 1 | **指揮官不是工程師** | 規格、PRD、決策由人下；程式碼由 DevSwarm 寫。Stack 必須 LLM 寫得最順手、最少坑 | 後端語言、ORM、前端框架 |
| 2 | **AI-first 開發團隊** | 沒有真人工程師組，DevSwarm 就是團隊。Python 因訓練語料壓倒性多，LLM 程式碼錯誤率最低 | Python over Go |
| 3 | **K8s 但晚一點** | Phase 4 連鎖才會 K8s。MVP 階段一台 VM + Docker Compose 即可 | 別過早 Service Mesh |
| 4 | **台灣 SMB 餐飲市場** | 客單低、單店利潤薄、不能燒雲端。LINE / ECPay / 街口 / 綠界 / iCHEF（轉檔） 必整合 | 在地化金流模組、低成本主機 |
| 5 | **低毛利容錯需求極高** | 真實損益每一塊錢都要對得起來 → 強型別、強事務、強型別檢核 | Pydantic 2 + SQLAlchemy 2 async + Postgres，不接受弱型別 |
| 6 | **未來要做 AI 嵌入式分析** | 客戶分群、需求預測、選址 → 需要向量檢索 | pgvector，不另接 Pinecone / Milvus |
| 7 | **多租戶（連鎖加盟）** | Phase 2 之後租戶數量會爆 | Postgres RLS + `tenant_id`，DB-per-tenant 絕對不選 |
| 8 | **零信任資安基線** | 個資法、未來 GDPR、PCI-DSS（金流） | 框架原生支援 OAuth2 / JWT / CSRF / CSP |

---

## 2. Option A — FastAPI + Next.js + Postgres + Redis（Python 全棧）

### 優點

| 面向 | 說明 |
|---|---|
| LLM 友善度 | Python 是 LLM 訓練語料的金字塔頂端。Claude Sonnet 在 FastAPI/SQLAlchemy 的 first-try 通過率遠高於 Go |
| 同語言 dogfood | DevSwarm 本身是 Python，產出的 RestSwarm 也是 Python → 同一個蜂群可自我擴展（之後新增 Agent 不用換 toolchain） |
| 型別系統 | Pydantic 2 + SQLAlchemy 2 async + mypy strict → 比 TypeScript 還嚴。Pydantic schema 又可直接餵 OpenAPI / 給前端產型別 |
| 生態 | Celery / RQ / Alembic / pytest / Locust / OpenTelemetry SDK 都極成熟 |
| AI / ML 整合 | scikit-learn, pandas, polars, numpy, langchain, sentence-transformers 全在主場 |
| Hiring（將來真要請人時） | Python 後端工程師供給遠多於 Go |

### 缺點

| 面向 | 說明 | 緩解 |
|---|---|---|
| 純效能 | 比 Go 慢 2–5×（CPU-bound） | 餐飲是 I/O-bound（DB/外部 API），FastAPI + asyncpg 足夠應付單店 1000 RPS |
| GIL | 多執行緒受限 | 用 async I/O + 多 worker process（uvicorn workers） |
| 部署體積 | Docker image 比 Go 大（~150MB vs ~20MB） | 用 `python:3.12-slim` + multi-stage build，控在 200MB 內可接受 |
| 啟動時間 | ~2-3 秒 | K8s readiness probe 設好就沒事 |

---

## 3. Option B — Go + Next.js + Postgres + Redis

### 優點

| 面向 | 說明 |
|---|---|
| 純效能 | 編譯型，CPU-bound 工作飛快 |
| 並發模型 | goroutine 直觀，無 GIL |
| 部署體積 | 單一 binary 部署，image 極小 |
| 記憶體 | 同負載下記憶體用量約 Python 的 1/3 |

### 缺點

| 面向 | 說明 |
|---|---|
| LLM 程式碼品質 | Go 訓練語料量 << Python。LLM 寫 Go 容易產出冗長 boilerplate、interface 設計失準 |
| 表達力 | Generics 雖然有了但仍生硬，DSL/反射很弱 → Pydantic 等級的 schema 庫只能自己拼 |
| AI / 資料生態 | gorgonia / golearn 規模遠不如 Python |
| Dogfood | DevSwarm 必須切 toolchain → 維護兩套 |
| Hiring | Go 工程師在台灣供給較稀薄 |
| ORM | GORM 比 SQLAlchemy 弱一個世代 |

---

## 4. Recommendation：Option A（FastAPI + Next.js + Postgres + Redis）

### 4.1 評分矩陣

權重 1–5，分數 1–5，加權總分越高越好。

| 評分項目 | 權重 | Option A (Python) | Option B (Go) | A 加權 | B 加權 |
|---|---:|---:|---:|---:|---:|
| LLM 程式碼產出品質 | 5 | 5 | 3 | 25 | 15 |
| 與 DevSwarm 同語言 dogfood | 5 | 5 | 2 | 25 | 10 |
| ORM / Schema 表達力 | 4 | 5 | 3 | 20 | 12 |
| 純執行效能 | 2 | 3 | 5 | 6 | 10 |
| 部署體積 / 啟動速度 | 2 | 3 | 5 | 6 | 10 |
| AI / 資料分析生態 | 4 | 5 | 2 | 20 | 8 |
| 台灣金流 SDK 可用性 | 3 | 4 | 3 | 12 | 9 |
| 整體生態成熟度 | 3 | 5 | 4 | 15 | 12 |
| Hiring（後備真人） | 2 | 5 | 3 | 10 | 6 |
| 維運心智負擔 | 3 | 4 | 4 | 12 | 12 |
| **總分** | — | — | — | **151** | **104** |

### 4.2 關鍵拍板理由（Key tiebreakers）

1. **DevSwarm 本身就是 Python**。同語言 stack 表示蜂群可以「吃自己的狗食」—— 之後產出的 RestSwarm 模組，DevSwarm 自己就能 debug、擴充、重構，不需要切第二套 toolchain。雙語言會把蜂群的維護負擔翻倍。
2. **LLM 在 Python 的 first-try 程式碼正確率最高**。我們的「核心生產力」不是工程師時薪，而是 LLM token / 修補回合數。Python = 最少回合 = 最少 token。
3. **Pydantic 2 + SQLAlchemy 2 async** 的型別嚴格度已可媲美 Go，且能直接生成 OpenAPI → 前端 TypeScript 型別 → 全鏈型別安全。
4. **AI 嵌入分析必走 Python**。Phase 3 的選址、區域競品分析、需求預測都是 pandas / numpy / sklearn 主場。同語言才能直接 import，不必額外架 microservice。

---

## 5. Concrete pinned stack（鎖版本，不接受隱性升版）

### 5.1 Backend

| 元件 | 版本 | 說明 |
|---|---|---|
| Python | **3.12.x** | 不上 3.13（生態未穩、GIL-free 還在實驗） |
| FastAPI | **0.115.x** | OpenAPI 3.1、Pydantic 2 原生支援 |
| Pydantic | **2.9.x** | Schema validation；模型 = 契約 |
| SQLAlchemy | **2.0.x (async)** | 強型別 ORM；不要回退 1.x |
| asyncpg | **0.30.x** | Postgres async driver；不要 psycopg2 |
| Alembic | **1.13.x** | Migration；每次 schema 改動必發 ADR |
| Celery | **5.4.x** | 重型 / 排程任務（採購預測、月結、報表批次） |
| Redis | **7.x** | broker + cache + rate-limit |
| httpx | **0.27.x** | 對外 API（ECPay / LINE / Google Maps） |
| structlog | **24.x** | 結構化 log；JSON 輸出給 Loki |
| pytest + pytest-asyncio | latest | 測試框架 |
| ruff + mypy strict | latest | Lint / 型別檢核；CI hard-gate |

### 5.2 Frontend

| 元件 | 版本 | 說明 |
|---|---|---|
| Next.js | **14.x App Router** | RSC + Server Actions；Phase 1 只做 admin UI |
| TypeScript | **5.5.x strict** | `noImplicitAny`、`strictNullChecks` 全開 |
| Tailwind CSS | **3.4.x** | utility-first；不引 CSS-in-JS |
| shadcn/ui | latest | Headless component；可被 LLM 直接複用 |
| TanStack Query | **5.x** | server state；不要 Redux |
| zod | **3.x** | client schema；與 Pydantic 對映 |
| next-intl | **3.x** | i18n；zh-TW 主、en 備 |

### 5.3 Database

| 元件 | 版本 | 用途 |
|---|---|---|
| PostgreSQL | **16.x** | 唯一交易型資料庫；參考 [`./04_data_schema.md`](./04_data_schema.md) |
| pgvector | **0.7+** | 客戶 embedding、菜單描述、選址向量 |
| PostGIS | **3.4+**（Phase 3 才啟用） | 門市地理、區域分析 |
| Postgres RLS | 原生 | 多租戶安全網；Phase 2 啟用 |
| pg_uuidv7 | 0.1+ | UUIDv7 主鍵；schema 文件已規定 |

### 5.4 Cache / Queue

| 元件 | 版本 | 用途 |
|---|---|---|
| Redis | **7.x** | Celery broker + session + rate-limit |
| Redis Streams | 原生 | 事件匯流；Phase 4 連鎖才開 |

### 5.5 Infra

| 階段 | 部署型態 |
|---|---|
| Phase 0–1 (MVP) | 單台 VM（Hetzner / GCP e2-medium）+ Docker Compose + Caddy reverse proxy |
| Phase 2 | 加一台讀 replica；加 S3-compatible（Cloudflare R2）做檔案 |
| Phase 3 | Cloudflare CDN + WAF；備援 region |
| Phase 4 | K8s（GKE 或 EKS），多店連鎖才上 |

| 元件 | 用途 |
|---|---|
| Cloudflare | CDN、WAF、DDoS 緩衝、DNS、Zero Trust |
| Cloudflare R2 / MinIO（dev） | S3-compatible object storage；菜單照、發票 PDF |
| Caddy | 自動 TLS；MVP 階段比 nginx 省心 |
| Docker / Compose | 容器化基線 |
| K8s | Phase 4+；Helm chart 由 DevSwarm DevOps Agent 生成 |

### 5.6 Observability

| 元件 | 用途 |
|---|---|
| OpenTelemetry SDK (Python) | trace / metric / log 統一收集 |
| Grafana | 儀表板 |
| Loki | log 聚合 |
| Tempo | distributed tracing |
| Prometheus | metric scrape（Phase 4 才需要） |
| Sentry | 例外即時告警；Phase 1 即上 |

### 5.7 Payments（台灣專屬）

| 通道 | 用途 | 接入優先序 |
|---|---|---|
| ECPay 綠界 | 信用卡、ATM、超商代收 | Phase 1 |
| LINE Pay | 主流行動支付 | Phase 1 |
| 街口支付 JKOPay | 在地高滲透率 | Phase 1 |
| 一卡通 Money / 悠遊付 | 補完支付通路 | Phase 2 |
| 信用卡 3DS 2.0 | PCI-DSS 合規 | Phase 2 |

> 共同走 ECPay 一條金流 hub 抽象層；上層業務不接觸 vendor API 細節。

### 5.8 電子發票（法遵）

| 項目 | 說明 |
|---|---|
| 平台 | 串接「大平台」電子發票 API（如：歐巴、ezPay、藍新） |
| 載具 | 手機條碼、自然人憑證、會員載具皆需支援 |
| 中獎對獎 | Phase 2 自動化 |
| 上傳財政部 | 透過大平台轉送，不直連 |

### 5.9 i18n / Locale

| 項目 | 設定 |
|---|---|
| 主語言 | **zh-TW** |
| 次語言 | en（後台、API 訊息） |
| 時區 | `Asia/Taipei` （DB UTC，view 層轉換） |
| 金額 | TWD，`numeric(14,4)`（與 schema 一致） |
| 日期格式 | ISO 8601（API）；民國年（可選顯示） |

---

## 6. Phase gating（階段守門）

每個階段交付物必須通過守門條件才推進下一階段。Stack 不過早膨脹。

| Phase | 期程 | 引入的新元件 | 守門條件 |
|---|---|---|---|
| **Phase 0 — DevSwarm** | 本 repo 現階段 | Python 3.12, LangGraph, Anthropic SDK only | 蜂群可產出帶測試的 Python module |
| **Phase 1 — 單店 MVP** | +2-3 月 | FastAPI, Postgres 16, Redis 7, Celery, Alembic, minimal Next.js admin | 真實損益日報 + BOM 即時扣料 + 五類分流 + 打卡 全部上線 |
| **Phase 2 — CRM + 行銷** | +3 月 | RLS 啟用、pgvector、LINE Messaging API、電子發票 | 多租戶安全測試通過；LINE 推播 ROI 可量化 |
| **Phase 3 — 地圖 + 區域數據** | +3 月 | PostGIS、Google Maps Platform、pandas 分析 pipeline | 區域競品報表自動跑 |
| **Phase 4 — 連鎖 / 加盟** | +3 月 | K8s、Redis Streams、權限矩陣強化 | 多店總部儀表板上線、SLA ≥ 99.9% |
| **Phase 5 — 全 AI 自主** | +6 月 | 預測引擎、優化建議引擎、自動排班器 | 三大閉環 24/7 跑滿 |

---

## 7. 明確延後 / 不做的（Explicit deferrals）

| 項目 | 延後到 | 理由 |
|---|---|---|
| 使用者認證體系 | Phase 2 | MVP 是單店單帳，先用 HTTP Basic + IP allowlist；上 RLS 同時導入 OIDC（Keycloak 或 Auth.js） |
| 多租戶（RLS 啟用） | Phase 2 | Phase 1 單店即可；schema 已預埋 `tenant_id`，啟用是 policy 而非結構改動 |
| 前端正式 UI | Phase 1 僅最小 admin | 真實前場用 POS 終端，後台用 minimal Next.js dashboard；正式設計師介入是 Phase 2 |
| Native mobile App | Phase 5+ | 前期用 PWA + LINE LIFF 蓋住員工與顧客場景 |
| GraphQL | 永久不做 | OpenAPI + TanStack Query 已夠；GraphQL 心智負擔不划算 |
| 微服務拆分 | Phase 4+ 才考慮 | MVP 走 modular monolith；提早拆會放大蜂群維運成本 |
| Kafka / Pulsar | 永遠先不上 | Redis Streams 撐到單店每秒上千事件沒問題 |
| 自建 LLM / fine-tune | Phase 5 之後 | 先把 Anthropic API 用好；自建 infra 是後續事 |
| 區塊鏈 / NFT 會員 | 不做 | 不在願景內 |

---

## 8. Anti-goals（明確不要的東西）

- **不要 ORM-less 直 SQL 路線**（DBAL/SQL string concat）— LLM 寫 SQL 容易 injection / 邏輯漏
- **不要 Flask / Django**（單體太重 + 非 async first）
- **不要 MongoDB**（餐飲業會計級交易必須 ACID）
- **不要 Prisma / Drizzle**（Node 端 ORM；我們後端不是 Node）
- **不要 server-side rendering 全做**（admin SPA-ish 即可，Phase 2 才開 SSR for SEO）
- **不要 monorepo turborepo 直接全套**（Phase 1 一個 backend repo + 一個 frontend repo 就好）

---

## 9. 與其他文件的關係

| 文件 | 關係 |
|---|---|
| [`./00_vision.md`](./00_vision.md) | 上游 SSOT；本文是它的技術翻譯 |
| [`./02_devswarm_architecture.md`](./02_devswarm_architecture.md) | 平行；本文管 RestSwarm（產出物），它管 DevSwarm（工廠） |
| [`./03_roadmap.md`](./03_roadmap.md) | 下游；引用本文的 Phase gating |
| [`./04_data_schema.md`](./04_data_schema.md) | 下游；schema 必須能在本文鎖定的 Postgres 16 / pgvector / RLS 上跑 |

---

## 10. 變更程序

任何對本文 §5（鎖版本）的修改，必須附 ADR（Architecture Decision Record），格式：

```
docs/adr/NNNN-<slug>.md

# ADR-NNNN: <title>
Status: proposed | accepted | superseded by NNNN
Date: YYYY-MM-DD
Decider: 指揮官
Context: ...
Decision: ...
Consequences: ...
```

未經 ADR，DevSwarm 與真人都不得偷偷升版。
