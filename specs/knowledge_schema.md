# Spec: Knowledge Base Schema (`knowledge_*` tables + Alembic migration)

> **Module name:** `restaurant_api.models.knowledge`
> **Owner domain:** AI / Knowledge / RAG infrastructure
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** SQLAlchemy 2.x async ORM + one Alembic revision
> **Models touched / added:** `restaurant_api/models/knowledge.py` (new),
> `restaurant_api/models/__init__.py` (re-exports),
> `restaurant_api/models/embeddings.py` (no schema change — reuse via `entity_type='knowledge_chunk'`),
> `restaurant_api/alembic/versions/<ts>_knowledge_base.py` (new)

---

## 1. Background

我們要把 NotebookLM 從「agent loop 的後端」降級為「人類研究工具」，把真正的知識軍火庫放回自家 PostgreSQL。理由（節錄 commander 的決策文）：

- 社群 NotebookLM MCP 走瀏覽器自動化，5–30s 延遲、DOM-coupled、無 audit、無 `tenant_id`、無 `audit_log`。
- 我們已經有 PostgreSQL 16 + pgvector 0.6 + FastAPI + async SQLAlchemy + 既有的 `embeddings` 表，**自建 RAG 是更短路徑，不是更長**。
- 同一份 retrieval 路徑將同時服務：投資人 Q&A、工程估價比對、店長 LINE bot、品牌文案產出、員工訓練教材。

本 spec 是 BUFF OS Week 1 的**唯一可交付**：把 3 張 knowledge_* 表打進 schema，含完整 Alembic migration、append-only ledger 規則、與既有 `embeddings` 表的關係定義。**不含** ingestion pipeline（→ `knowledge_ingestion.md`）、**不含** retrieval router（→ 後續 `knowledge_router.md`）。

---

## 2. Goal

新增三張表 `knowledge_documents`、`knowledge_chunks`、`knowledge_queries`，並把已存在的 `embeddings` 表透過 `entity_type='knowledge_chunk'` 的 polymorphic 指標納入 retrieval 路徑。Alembic migration 必須：

1. 在新表上建立必要的 unique / index / FK / CHECK。
2. 對 `knowledge_queries` 套用 append-only 鐵律（`ON UPDATE / ON DELETE DO INSTEAD NOTHING`），與既有 `stock_movements`、`audit_log`、`customer_points_ledger` 同等級別。
3. 在新表上掛 `trg_touch_updated_at` 觸發器（僅 `knowledge_documents`、`knowledge_chunks` 有 `updated_at`）。
4. `alembic upgrade head` 與 `alembic downgrade -1` 對 DB 來回乾淨。

---

## 3. Scope

### 3.1 In scope（本 spec）

- 3 張新表的 SQLAlchemy 2.x ORM（`Mapped[]` syntax）
- 1 個新 Enum `KnowledgeScope`
- 1 份 Alembic migration（上下行皆可）
- 既有 `embeddings` 表的「reuse 約定」文件化（**不改 schema**，只在新表的 docstring 寫清楚）
- `tests/models/test_knowledge_models.py` 涵蓋 AC-1 ~ AC-12

### 3.2 Out of scope（延後到後續 spec）

- Ingestion pipeline（PDF → chunks → embeddings）→ `knowledge_ingestion.md`
- Retrieval API（`/knowledge/search`、`/knowledge/ask`）→ 後續 router spec
- MCP server 工具定義 → 後續 spec
- 跨 tenant 共享知識（公開法規、政府公告）→ 後續模組
- BM25 / `tsvector` GIN 索引（v2 加）→ 後續 spec
- HNSW vs IVFFlat 切換 → 既有 `embeddings` 表已使用 IVFFlat，本次沿用，不調整
- 多模態（圖、音、影）embedding → 後續
- 跨 chunk relation graph / parent-child chunking → 後續

---

## 4. Tables

### 4.1 `knowledge_documents`

> 一個 source artifact = 一筆 row。PDF / Markdown / Drive 檔 / Notion page / LINE 對話 dump / 投資人 email thread，**任何**外部資料都收斂到這張表。

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | `UUID` (uuidv7) | no | `uuid7()` | PK |
| `tenant_id` | `UUID` | no | — | FK `tenants.id` ON DELETE RESTRICT，與其他業務表一致 |
| `scope` | `knowledge_scope` enum | no | — | 對應戰場分群（見 §4.4 enum） |
| `source_type` | `String(32)` | no | — | `pdf`、`markdown`、`docx`、`pptx`、`xlsx`、`html`、`google_doc`、`notion`、`line_chat`、`email`、`other` |
| `source_uri` | `Text` | no | — | 原檔位置；Drive 用 `drive://<file_id>`、本機 `file://...`、URL `https://...` |
| `title` | `String(512)` | no | — | 人類可讀標題（檔名或第一個 H1） |
| `sha256` | `String(64)` | no | — | 原檔內容 hash，**idempotency key** |
| `byte_size` | `BigInteger` | no | — | 原檔 bytes |
| `mime_type` | `String(128)` | yes | `null` | RFC 6838 |
| `language` | `String(8)` | yes | `null` | BCP-47，`zh-Hant` / `en` 為主 |
| `ingested_at` | `timestamptz` | no | `now()` | 本系統首次寫入時間 |
| `source_modified_at` | `timestamptz` | yes | `null` | 原檔最後修改時間（從 Drive metadata 或檔案 mtime） |
| `meta` | `JSONB` | no | `'{}'` | 原檔 metadata（作者、頁數、Drive owner、Notion DB id 等） |
| `is_sensitive` | `Boolean` | no | `false` | True = 含敏感資訊（合約、薪資、身份證、銀行帳號）；ingestion 端決定，retrieval 端用此擋人 |
| `tags` | `ARRAY(Text)` | no | `'{}'` | 自由標籤，例如 `['投資人對話','張先生','2026-Q2']` |
| `created_at` / `updated_at` / `deleted_at` | `timestamptz` | — | mixin 標準 | `TimestampedMixin` + `SoftDeleteMixin` |

**Indexes:**
- PK: `id`
- `ix_knowledge_documents_tenant_id` (mixin 給的)
- `ix_knowledge_documents_tenant_scope_ingested`：`(tenant_id, scope, ingested_at DESC)` — 「列出募資戰場最新 50 份」
- `uq_knowledge_documents_tenant_sha256`：UNIQUE `(tenant_id, sha256)` where `deleted_at IS NULL` — **同租戶同雜湊 = 同檔**，idempotent
- `ix_knowledge_documents_tags`：GIN `(tags)`
- `ix_knowledge_documents_meta`：GIN `(meta)` — JSONB containment 查詢用

**Constraints:**
- CHECK `byte_size >= 0`
- CHECK `length(sha256) = 64`
- CHECK `source_type IN (closed set above)` — 用 PostgreSQL CHECK 或 enum 二選一；本 spec 選 CHECK，避免 enum 後續加值要 migration（語意上這欄是動詞集，不是狀態機）

### 4.2 `knowledge_chunks`

> 一份 document 切成 N 個 chunk；retrieval 的最小單位。chunk 的 embedding **不存這張表**，存在既有 `embeddings` 表，透過 `(entity_type='knowledge_chunk', entity_id=chunks.id)` 對齊。

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | `UUID` | no | `uuid7()` | PK |
| `tenant_id` | `UUID` | no | — | FK + index |
| `document_id` | `UUID` | no | — | FK `knowledge_documents.id` ON DELETE CASCADE |
| `chunk_idx` | `Integer` | no | — | 0-based，同 document 內單調遞增 |
| `text` | `Text` | no | — | chunk 內文（已 normalize 空白、去掉 page header/footer） |
| `text_sha256` | `String(64)` | no | — | `text` 的 hash，避免 re-embed 已存在內容 |
| `token_count` | `Integer` | no | — | 用 tiktoken / Voyage tokenizer 估，retrieval budget 用 |
| `char_count` | `Integer` | no | — | `length(text)` 的快照 |
| `page_from` | `Integer` | yes | `null` | PDF 頁碼起 |
| `page_to` | `Integer` | yes | `null` | PDF 頁碼迄 |
| `section_path` | `Text` | yes | `null` | 例如 `Chapter 2 > 募資結構 > 估值假設` |
| `meta` | `JSONB` | no | `'{}'` | chunk 級的雜項：是否含表格、是否含金額、是否含人名等 |
| `created_at` / `updated_at` | `timestamptz` | — | — | `TimestampedMixin`；chunks 不軟刪除（隨 document cascade） |

**Indexes:**
- PK: `id`
- `ix_knowledge_chunks_tenant_id`（mixin）
- `uq_knowledge_chunks_document_idx`：UNIQUE `(document_id, chunk_idx)` — chunk 順序唯一
- `uq_knowledge_chunks_tenant_textsha`：UNIQUE `(tenant_id, text_sha256)` — 同租戶同內容 chunk 去重（同份簡報跨 deck 重複的「公司願景」段落只 embed 一次）
- `ix_knowledge_chunks_document_id`
- `ix_knowledge_chunks_token_count`：BTREE — retrieval budget 計算用

**Constraints:**
- CHECK `chunk_idx >= 0`
- CHECK `token_count >= 0`
- CHECK `char_count >= 0`
- CHECK `length(text) > 0`

**關鍵設計（必須在 docstring 寫清楚）：**

```text
chunks 不直接存 embedding vector。embedding 寫到既有 `embeddings` 表：
    INSERT INTO embeddings (entity_type, entity_id, vector, model_name, ...)
    VALUES ('knowledge_chunk', <chunks.id>, <vector>, 'voyage-3-large', ...);

理由：
1. 一個 ANN index 服務所有可被 embedded 的實體（menu_item / customer / chunk / ...）
2. 同一 chunk 可被多個 model_version embed（A/B 比較），靠 (entity_id, model_version) 唯一
3. 換 embedding 模型不需要動 chunks 表
4. retrieval 走 JOIN：
     SELECT c.* FROM embeddings e JOIN knowledge_chunks c ON c.id = e.entity_id
     WHERE e.entity_type = 'knowledge_chunk'
       AND e.tenant_id = :tid
       AND e.model_name = :m AND e.model_version = :v
     ORDER BY e.vector <=> :query_vec LIMIT :k;
```

### 4.3 `knowledge_queries`

> RAG 稽核軌跡。**append-only ledger**，與 `audit_log` / `stock_movements` 同等級別。每一次 Claude / Codex 透過 MCP 對知識庫提問都寫一筆。

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | `UUID` | no | `uuid7()` | PK |
| `tenant_id` | `UUID` | no | — | FK + index |
| `actor_kind` | `String(16)` | no | — | `claude` / `codex` / `human` / `cron` / `mcp` |
| `actor_id` | `String(128)` | yes | `null` | session id / employee id / agent name |
| `query_text` | `Text` | no | — | 原始問題（已 redact 敏感詞，redaction 由 ingestion service 統一） |
| `scope_filter` | `ARRAY(Text)` | no | `'{}'` | 本次限定的 scope，例如 `['funding','engineering']`；空陣列 = 全 scope |
| `tag_filter` | `ARRAY(Text)` | no | `'{}'` | 同上，標籤 filter |
| `retrieved_chunk_ids` | `ARRAY(UUID)` | no | `'{}'` | retrieval 命中的 chunk id（依分數排序） |
| `retrieved_scores` | `ARRAY(Numeric(8,6))` | no | `'{}'` | 對應分數（cosine similarity 或 hybrid score）；長度 = chunk_ids |
| `model_name` | `String(64)` | yes | `null` | 回答時呼叫的 LLM 名稱（例如 `claude-opus-4-7`） |
| `prompt_tokens` | `Integer` | yes | `null` | LLM 計費快照 |
| `completion_tokens` | `Integer` | yes | `null` | 同上 |
| `latency_ms` | `Integer` | yes | `null` | retrieval + generation 總耗時 |
| `answer_text` | `Text` | yes | `null` | LLM 最終回答；如果 retrieval-only 不過 LLM 則 null |
| `answer_citations` | `JSONB` | no | `'{}'` | 結構化引用：`[{chunk_id, doc_id, score, span: [start,end]}]` |
| `was_blocked` | `Boolean` | no | `false` | True = 因為觸到敏感資料 deny-list 而拒答 |
| `blocked_reason` | `Text` | yes | `null` | 若 was_blocked，寫原因（例如 `sensitive_doc_filtered`） |
| `created_at` | `timestamptz` | no | `now()` | **只有 created_at，不繼承 TimestampedMixin**（append-only） |

**Indexes:**
- PK: `id`
- `ix_knowledge_queries_tenant_id`（mixin）
- `ix_knowledge_queries_tenant_actor_time`：`(tenant_id, actor_kind, created_at DESC)`
- `ix_knowledge_queries_tenant_time`：`(tenant_id, created_at DESC)`
- `ix_knowledge_queries_chunk_ids`：GIN `(retrieved_chunk_ids)` — 反查「這個 chunk 被引用過幾次」

**Constraints:**
- CHECK `actor_kind IN ('claude','codex','human','cron','mcp')`
- CHECK `array_length(retrieved_chunk_ids,1) IS NOT DISTINCT FROM array_length(retrieved_scores,1)`（長度對齊）
- CHECK `(was_blocked = false) OR (blocked_reason IS NOT NULL)`
- **DB-level rules**:
  - `CREATE RULE no_update_knowledge_queries AS ON UPDATE TO knowledge_queries DO INSTEAD NOTHING;`
  - `CREATE RULE no_delete_knowledge_queries AS ON DELETE TO knowledge_queries DO INSTEAD NOTHING;`

### 4.4 Enum

```python
class KnowledgeScope(enum.StrEnum):
    FUNDING = "funding"          # 募資戰情、股權、估值、投資人 Q&A
    ENGINEERING = "engineering"  # 工程裝修、估價、設備、進度
    SOP = "sop"                  # 營運 SOP、訓練、人力配置
    BRAND = "brand"              # 品牌定位、文案、視覺、禁用詞
    COMPETE = "compete"          # 競品、市場、商圈
    SUBSIDY = "subsidy"          # 政府貸款、補助、資格
    SYSTEM = "system"            # 內部 AI 系統、Codex、MCP、Notion 結構
    GENERAL = "general"          # 兜底；不該長期使用，但避免 ingestion fail
```

存 DB 時以 `SQLEnum(KnowledgeScope, name="knowledge_scope", native_enum=False, length=24)` —— 與專案其他 enum 慣例一致（不用原生 PG enum，方便加值）。

---

## 5. Migration（Alembic）

### 5.1 上行（`upgrade()`）

1. 建立 3 張新表（順序：`knowledge_documents` → `knowledge_chunks` → `knowledge_queries`）。
2. 建立所有 unique / partial unique / GIN / BTREE index。
3. 對 3 張表的 `tenant_id` 加 FK 到 `tenants(id)` ON DELETE RESTRICT。
4. 對 `knowledge_documents` 與 `knowledge_chunks` 掛 `trg_touch_updated_at` 觸發器（與其他業務表一致的命名與函式）。
5. 對 `knowledge_queries` 建立 append-only rules：
   ```sql
   CREATE RULE no_update_knowledge_queries AS ON UPDATE TO knowledge_queries DO INSTEAD NOTHING;
   CREATE RULE no_delete_knowledge_queries AS ON DELETE TO knowledge_queries DO INSTEAD NOTHING;
   ```
6. 寫入一筆 `meta` 註記到 alembic 的 revision comment 描述「Knowledge base seed schema for BUFF OS」。

### 5.2 下行（`downgrade()`）

1. **先**刪 append-only rules，否則後續 drop 會 ROW_COUNT=0 但不報錯。
2. 刪觸發器。
3. 依反向順序 drop index → drop table（先 `knowledge_queries`、再 `knowledge_chunks`、最後 `knowledge_documents`，FK 才不會衝）。
4. 不動 `embeddings` 表（reuse 約定純文件化）。

### 5.3 不允許

- 不准把 vector 欄位塞進 `knowledge_chunks`（違反「reuse `embeddings`」設計）。
- 不准用 `Numeric` 存 token count（用 `Integer`）。
- 不准在 migration 裡塞 data seed（schema 與 seed 分開，seed 由 `scripts/seed_knowledge.py` 處理）。
- 不准跳過 `tenant_id` index（mixin 已給，但要在 migration 中明確 create_index）。

---

## 6. Public interface

ORM 從 `restaurant_api.models` 直接 import：

```python
from restaurant_api.models import (
    KnowledgeDocument,
    KnowledgeChunk,
    KnowledgeQuery,
    KnowledgeScope,
)
```

`restaurant_api/models/__init__.py` 必須加上對應 re-export，並排在 `Embedding` 區段附近（保留註釋 `# knowledge base (BUFF OS)`）。

---

## 7. Acceptance Criteria

> 每一條對應一個 pytest test function；命名 `test_knowledge_ac_NN_*`。
> 測試走 `tests/conftest.py` 既有 `async_session` + `seed_tenant` fixture，SAVEPOINT 隔離。

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | 三張表存在且可 INSERT/SELECT | 用 ORM 各塞 1 筆 → SELECT 回來欄位完整。 |
| AC-2 | `tenant_id` 必填 | 缺 `tenant_id` 的 INSERT 應拋 `IntegrityError`。 |
| AC-3 | `knowledge_documents` sha256 idempotency | 同 `tenant_id` + 同 `sha256` 第二次 INSERT 應拋 unique violation（partial unique，僅 `deleted_at IS NULL`）。 |
| AC-4 | 軟刪後同 sha256 可再 INSERT | 把第一份 soft-delete (`deleted_at = now()`) 後，第二份 INSERT 應成功（partial unique 排除 soft-deleted row）。 |
| AC-5 | `knowledge_chunks` cascade with document | 刪 `knowledge_documents` row → 對應 `knowledge_chunks` 全刪。 |
| AC-6 | `knowledge_chunks` chunk_idx 唯一 | 同 `document_id` 同 `chunk_idx` 第二次 INSERT 應拋 unique violation。 |
| AC-7 | `knowledge_chunks.text_sha256` 同租戶去重 | 同 `tenant_id` 同 `text_sha256` 第二次 INSERT 應拋 unique violation。 |
| AC-8 | `KnowledgeScope` enum 拒收非法值 | INSERT `scope='garbage'` 應在 ORM 層或 DB 層被擋。 |
| AC-9 | `knowledge_queries` append-only：UPDATE 無效 | INSERT → 嘗試 UPDATE → ROW_COUNT = 0 且資料未變。 |
| AC-10 | `knowledge_queries` append-only：DELETE 無效 | INSERT → 嘗試 DELETE → ROW_COUNT = 0 且 row 仍存在。 |
| AC-11 | `retrieved_chunk_ids` / `retrieved_scores` 長度一致 | 兩陣列長度不同的 INSERT 應拋 CHECK violation。 |
| AC-12 | `embeddings` reuse 驗證 | 建 1 個 chunk → 在既有 `embeddings` 表用 `entity_type='knowledge_chunk'` + `entity_id=<chunk.id>` INSERT 1 筆 → 用 cosine `<=> ` 查得回來。**不**在 `knowledge_chunks` 表新增 vector 欄位。 |
| AC-13 | Alembic round-trip | `alembic upgrade head` 後 `alembic downgrade -1` 不報錯，再次 `upgrade head` 也不報錯。 |
| AC-14 | 觸發器掛上 | UPDATE `knowledge_documents.title` 後 `updated_at` 應自動 bump（透過既有 `trg_touch_updated_at`）。 |

---

## 8. Edge cases（必須在測試中列舉）

- **空 `tags` / 空 `scope_filter`**：應視為「無 filter」，預設值為 `'{}'`（PG empty array），不可為 `NULL`。
- **`source_modified_at` 未來時間**：不擋；上游 metadata 偶爾會給未來時間（時區錯位），ingestion 端負責修，schema 不擋。
- **`text` 含 4-byte UTF-8（emoji、罕用字）**：DB encoding 必須 `UTF8`，否則 INSERT 會炸。本 spec 預設 DB 已 UTF8（與 `restaurant_api/docker-compose.yml` 一致）。
- **大 `text`（> 1MB）**：不擋，但 ingestion spec 會限制；schema 用 `Text` 不設 length。
- **`chunk_idx` 跳號**（0, 2, 3）：允許，因為 ingestion 可能會 skip 空 page；unique index 是 `(document_id, chunk_idx)`，不是 monotonic check。
- **`is_sensitive=True` 文件的 chunks 也照樣寫入**：擋人是 retrieval 層的事；ingestion 仍要寫進來，否則無法稽核「我們有沒有不小心吃了合約」。

---

## 9. Constraints（hard requirements）

- **Python 3.12**，SQLAlchemy 2.x `Mapped[]` 語法，**不准**用舊式 `Column(...)` declarative。
- **金錢欄位**：本 spec 不存金錢；若未來 chunk 級要存「文件抽出的金額」，走 `meta` JSONB 不另開欄位。
- **時間欄位**：全 `timestamptz`，DB 存 UTC，response 端用 `Asia/Taipei`（與專案慣例一致）；本 spec 是 schema，不涉 response。
- **UUIDv7**：所有 PK 一律 `default=uuid7`（用 `restaurant_api.models.base.uuid7`）。
- **多租戶**：3 張表都繼承 `TenantScopedMixin`。
- **append-only**：`knowledge_queries` 用 DB rule 鎖死，**且**不繼承 `TimestampedMixin`（無 `updated_at`）。
- **無 raw `HTTPException`**：本 spec 不涉 router；服務層若違反此規則由後續 spec 規範。
- **Pydantic v2**：本 spec 不涉 schema/響應 model；若 ORM `__init__` 要接受 dict 則走 SQLAlchemy 原生，不混 Pydantic。
- **`ruff`、`pyright basic`、`pytest`**：全綠才算 Done。
- **`alembic check`**：須通過（no pending autogenerate diff after migration）。

---

## 10. Out of scope（重申，避免 drift）

- Ingestion pipeline（單獨 spec）
- Retrieval API（單獨 spec）
- MCP server（單獨 spec）
- BM25 / `tsvector` 索引
- Cross-tenant shared knowledge
- 文件抽出後的「結構化欄位」（金額、日期、人名）→ 走 `meta` JSONB，不另設欄位
- LINE bot / 店長前端對接
- Embedding 模型切換 / multi-model parallel embedding（既有 `embeddings` 表 schema 已支援，本 spec 不動）

---

## 11. Connection to other modules

| Module | 介面 |
|---|---|
| `restaurant_api.models.embeddings` | reuse via `(entity_type='knowledge_chunk', entity_id=chunks.id)`；**不改 schema** |
| `restaurant_api.models.tenants` | `tenant_id` FK |
| `restaurant_api.models.audit.AuditLog` | 同等級別的 append-only ledger；本表是 RAG 層的 audit |
| `services.knowledge_ingestion`（待寫） | 寫入 `knowledge_documents` + `knowledge_chunks`，呼叫 embedding API 寫 `embeddings` |
| `routers.knowledge`（待寫） | retrieval；每次 query 寫 `knowledge_queries` 一筆 |
| `mv_daily_pnl` etc. | 無直接關係 |
| Alembic head | 本次 migration 須 `down_revision` 指向 `20260619_215040_ugc_submissions.py` 的 revision id |

---

## 12. Done = all of:

1. `restaurant_api/models/knowledge.py` 存在，type-checks cleanly (`pyright`), no unused imports, ruff 全綠。
2. `restaurant_api/models/__init__.py` 已 re-export 3 個 class + 1 個 enum。
3. 1 份 Alembic migration 位於 `restaurant_api/alembic/versions/<ts>_knowledge_base.py`，`alembic upgrade head` 與 `alembic downgrade -1` 來回乾淨。
4. `tests/models/test_knowledge_models.py` 包含 AC-1 ~ AC-14 對應 test functions。
5. `make full-check` 全綠（ruff + pyright + pytest + alembic-check + db-smoke）。
6. `knowledge.py` 頂部 docstring 含「為什麼 chunk 不存 vector、要走 embeddings 表」的設計理由（10 行內）。
7. 本 spec 中所有 DB-level constraint（CHECK、partial unique、GIN、append-only RULE、trigger）在 migration 內可被 `psql \d+ knowledge_*` 看到。

— end of spec —
