# Spec: Knowledge Ingestion Service (`services.knowledge_ingestion`)

> **Module name:** `restaurant_api.services.knowledge_ingestion`
> **Owner domain:** AI / Knowledge / RAG infrastructure
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** async service module + tests; no FastAPI router in this spec
> **Depends on:** `specs/knowledge_schema.md` (must be implemented first)
> **Models touched:** `KnowledgeDocument`, `KnowledgeChunk`, `Embedding` (existing)
> **Files added:**
> - `restaurant_api/services/knowledge_ingestion/__init__.py`
> - `restaurant_api/services/knowledge_ingestion/extract.py`
> - `restaurant_api/services/knowledge_ingestion/chunk.py`
> - `restaurant_api/services/knowledge_ingestion/embed.py`
> - `restaurant_api/services/knowledge_ingestion/redact.py`
> - `restaurant_api/services/knowledge_ingestion/pipeline.py`
> - `tests/services/test_knowledge_ingestion.py`

---

## 1. Background

`knowledge_schema.md` 把表立起來，但表是空的。本 spec 是「**怎麼把原檔變成可被 retrieval 命中的 chunk + embedding**」的單一可信流程。流程必須是 idempotent、可斷點續跑、可審計、可阻擋敏感資料、且和既有 `audit_service.audit()`、`tenant_id` 多租戶、`audit_log` append-only 三項既有法則完全對齊。

策略決策（從 commander 的分析摘出）：

- **NotebookLM 是研究實習生**，不在 agent loop 裡。本 ingestion 只負責把 Google Drive / 本機 / URL 上的原檔變成 RAG-ready data；NotebookLM 上看到「值得進系統」的資料，**手動匯出到 Drive 指定資料夾**，由本 pipeline 自動 pull。
- **Markdown 是 lingua franca**。所有來源透過 `markitdown` 統一轉成 markdown，再切 chunk。CLAUDE.md「檔案攝取」章節已埋好這個工具鏈，我們延伸它。
- **`is_sensitive` 是 deny-list 的標籤，不是過濾器**。敏感文件**仍然進 DB**，因為要稽核「我們有沒有不小心吃了合約」。擋人是 retrieval 層的事，本 spec 只負責**正確標籤**。
- **embedding 走既有 `embeddings` 表**，不在 `knowledge_chunks` 上加 vector 欄位（schema spec 已定）。

---

## 2. Goal

提供一個 async 服務 `ingest_source(source: IngestionSource) -> IngestionResult`：

1. 從 source（檔案路徑 / URL / Drive id / inline bytes）取得原檔。
2. 計算 `sha256`；若 `(tenant_id, sha256)` 已有非軟刪除文件 → 短路回傳「duplicate」結果（**不**重複 embedding 計費）。
3. 用 markitdown 轉 markdown（其他格式 → markdown）；純文字 / 已是 markdown 走 fast path。
4. 套 `redact()` 把命中 deny-list 正則的 token 替換為 `[REDACTED:<kind>]`；同步把整份文件標為 `is_sensitive=True` 若命中任一 deny-list 規則。
5. `chunk()` 把 markdown 切成 N 個 chunk（語意切分，見 §6）。
6. `embed()` 對每個 chunk 呼叫 embedding API（預設 `voyage-3-large`，可配置）；chunk-level idempotency 用 `(tenant_id, text_sha256)`。
7. 用**單一** transaction 寫入 `knowledge_documents` + N × `knowledge_chunks` + N × `embeddings`。
8. 寫 `audit_log` 1 筆 action=`knowledge.ingested`。
9. 回傳 `IngestionResult`（含 `document_id`、`chunk_count`、`is_duplicate`、`is_sensitive`、`tokens_embedded`、`cost_usd`）。

---

## 3. Scope

### 3.1 In scope

- `IngestionSource` / `IngestionResult` Pydantic schemas
- 4 個來源 adapter：`file://`、`https://`、`drive://<file_id>`、`inline:` (bytes)
- markitdown 包裝（async subprocess，超時 60s，記憶體上限 1GB）
- 純函式 `chunk(markdown: str, *, max_tokens: int, overlap: int) -> list[Chunk]`
- 純函式 `redact(text: str) -> tuple[str, list[RedactionHit]]`
- embedding client（`anthropic` SDK 已存在；voyage / openai 為可選 backend，本 spec 預設 voyage，可由 env 切換）
- pipeline 編排（async，single-DB-txn 寫入）
- audit log 寫入（透過既有 `services.audit_service.audit()`）
- 12 條 acceptance criteria + 對應 pytest
- **無**新 alembic migration（schema 由前一 spec 提供）

### 3.2 Out of scope

- FastAPI router（`POST /knowledge/ingest`）→ 下一個 spec
- Drive 自動排程（cron / APScheduler 觸發）→ 下一個 spec（先做手動觸發）
- 多模態 embedding（圖、表、音）
- 「跨 chunk 主從關係」（parent-child chunking）
- 重複 chunk 在「跨文件」的去重視覺化（schema 已能去重，UI 是後續事）
- OCR（掃描檔走 markitdown LLM 模式，需另一個 `OPENAI_API_KEY`；本 spec **不**啟用，請呼叫端事先 OCR 後再餵 markdown）
- 自動翻譯 / 多語切分
- LangChain / LlamaIndex 任何依賴（**禁止**，與專案無關，直接讓我們重新背一坨抽象）

---

## 4. Inputs — Pydantic schemas

所有 input model：`ConfigDict(frozen=True, strict=True)`；所有 `Decimal` / 數值欄位拒 float（與 `orders_router.md`、`profit_calc.md` 慣例一致）。

```python
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class IngestionSource(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    tenant_id: UUID
    scope: Literal[
        "funding", "engineering", "sop", "brand",
        "compete", "subsidy", "system", "general",
    ]
    # exactly one of the following four reference fields must be set
    file_path: str | None = Field(default=None, max_length=1024)
    url: str | None = Field(default=None, max_length=2048, pattern=r"^https://")
    drive_file_id: str | None = Field(default=None, max_length=128)
    inline_bytes_b64: str | None = None  # base64-encoded; useful for tests + LINE attachments

    # human metadata
    title: str | None = Field(default=None, max_length=512)
    tags: list[str] = Field(default_factory=list, max_length=32)
    source_modified_at: datetime | None = None  # tz-aware UTC; naive → 422
    explicit_sensitive: bool = False             # caller marks the doc as sensitive regardless of regex hits

    # ingestion knobs
    max_tokens_per_chunk: int = Field(default=512, ge=64, le=4096)
    chunk_overlap_tokens: int = Field(default=64, ge=0, le=512)
    embed_model: Literal[
        "voyage-3-large",
        "voyage-3",
        "text-embedding-3-large",
        "text-embedding-3-small",
    ] = "voyage-3-large"
    dry_run: bool = False  # if True: extract+chunk+redact, but don't embed, don't write DB
```

**Validation rules:**

| Field | Rule |
|---|---|
| Exactly one source ref | `file_path` xor `url` xor `drive_file_id` xor `inline_bytes_b64` |
| `tags` | each ≤ 64 chars; no whitespace-only; lowercased on the way in |
| `source_modified_at` | must be tz-aware; naive → `ValidationError` |
| `chunk_overlap_tokens` | must be `< max_tokens_per_chunk` |
| `inline_bytes_b64` | decoded size ≤ 200 MB（與 NotebookLM 同等級，避免一個 PDF 把 worker 撐爆） |

---

## 5. Output schema

```python
class RedactionHit(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["bank_account","national_id","credit_card","passport","api_key","unknown"]
    count: int  # ≥ 1

class IngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: UUID
    is_duplicate: bool             # True iff (tenant_id, sha256) already existed and was not soft-deleted
    is_sensitive: bool              # final value written to knowledge_documents.is_sensitive
    chunk_count: int                # rows added to knowledge_chunks; 0 if duplicate
    embedding_count: int            # rows added to embeddings; 0 if duplicate OR dry_run
    redactions: list[RedactionHit]  # empty if no hits; non-empty implies is_sensitive=True
    tokens_embedded: int            # sum across chunks (estimated by tokenizer)
    cost_usd: Decimal               # estimated; 0 on duplicate / dry_run
    extract_ms: int
    embed_ms: int
    write_ms: int
```

`cost_usd` 計算公式（spec 鎖死，方便回測）：

```text
voyage-3-large    = tokens * 0.18 / 1_000_000  USD
voyage-3          = tokens * 0.06 / 1_000_000  USD
text-embedding-3-large = tokens * 0.13 / 1_000_000 USD
text-embedding-3-small = tokens * 0.02 / 1_000_000 USD
```

回傳前 `quantize(Decimal("0.000001"))`。

---

## 6. Chunking algorithm

```python
def chunk(
    markdown: str,
    *,
    max_tokens: int,
    overlap: int,
    tokenizer: TokenCounter,
) -> list[Chunk]: ...
```

**規則（鎖死，方便 reproducible）：**

1. 先以 markdown 標題 (`#`, `##`, `###`) 切大段；每段帶 `section_path`（最近三層標題用 ` > ` 串起來）。
2. 每大段內以**句子**為單位累積，直到 `token_count > max_tokens` 為止；該段成為一個 chunk。
3. 下一個 chunk 從上一個 chunk 尾端 **N tokens 的尾巴重疊**（N = `overlap`），用以保留跨段語意。
4. PDF 場景：markitdown 會把 page break 標成 `<!-- page X -->` 註解；chunker 解析出 `page_from` / `page_to` 寫入 chunk meta。
5. 表格（markdown table）獨立成 chunk（不切碎）；超過 `max_tokens` 也保留為單一 chunk，並在 `meta` 寫 `has_table: true`。
6. 程式碼區塊 (` ``` `) 同上，不切碎，meta 寫 `has_code: true`。

**為什麼鎖死語意切分**：方便對齊 retrieval 的命中度量；future 換 chunker 要單獨開 spec、重 embed。

---

## 7. Redaction deny-list

```python
DENY_LIST = [
    # 台灣金融
    (r"\b\d{14}\b",            "bank_account"),       # 14-digit account
    (r"\b\d{16}\b",            "credit_card"),        # 16-digit card
    (r"\b[A-Z]{2}\d{7}\b",     "passport"),           # passport
    (r"\b[A-Z][12]\d{8}\b",    "national_id"),        # 身份證字號
    # 機密 token
    (r"\bsk-[A-Za-z0-9]{20,}\b",        "api_key"),   # OpenAI-ish
    (r"\bsk-ant-[A-Za-z0-9-_]{20,}\b",  "api_key"),   # Anthropic
    (r"\bghp_[A-Za-z0-9]{30,}\b",       "api_key"),   # GitHub
    (r"\bAIza[A-Za-z0-9_\-]{30,}\b",    "api_key"),   # Google
]
```

**行為：**

- 命中 → 用 `[REDACTED:<kind>]` 取代原 token。
- 命中**任一**規則 → 整份文件 `is_sensitive=True`（與 `IngestionSource.explicit_sensitive` 取 OR）。
- 命中統計回傳於 `IngestionResult.redactions`。
- redaction 是**就地替換**，redacted 後的文字才會去 chunk + embed + 落 DB。原檔不留存於 DB（只留 `source_uri` 指向原檔位置）。
- 規則表列為**模組層級常數** `DENY_LIST`，未來新增規則要改這支模組 + 加測試。

---

## 8. Public interface

```python
async def ingest_source(
    source: IngestionSource,
    *,
    session: AsyncSession,
    embedding_client: EmbeddingClient,
    now: datetime | None = None,  # for deterministic tests
) -> IngestionResult:
    """One-shot ingestion. Single async DB transaction; rollback on any failure.

    Raises:
        IngestionDeniedError: source bytes > 200MB cap, or unsupported mime type.
        IngestionExtractError: markitdown subprocess failed / timed out.
        IngestionEmbedError: embedding API failed after 3 retries (exponential 1s/4s/16s).
        IntegrityError: passes through if some other writer collided with our (tenant_id, sha256).
    """
```

`EmbeddingClient` 是一個 `Protocol`：

```python
class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...
    @property
    def vector_dim(self) -> int: ...
```

預設實作 `VoyageEmbeddingClient`（HTTP via `httpx`）。測試用 `FakeEmbeddingClient`（吐固定 1536-dim 隨機向量 + 可預期 token count）。

---

## 9. Transaction semantics（關鍵）

一次 `ingest_source` 呼叫 = **一個** DB transaction。

順序：
1. SELECT `knowledge_documents` WHERE `(tenant_id, sha256)` AND `deleted_at IS NULL` LIMIT 1。
2. 若命中 → 不寫任何 row，直接回 `IngestionResult(is_duplicate=True, ...)`。
3. 若未命中 → INSERT `knowledge_documents` → INSERT N × `knowledge_chunks` → INSERT N × `embeddings`。
4. INSERT `audit_log` 一筆：
   ```
   action="knowledge.ingested",
   target_table="knowledge_documents",
   target_id=<doc.id>,
   after={"scope": ..., "chunk_count": N, "is_sensitive": ..., "redactions": [...]},
   reason=<source.title or source_uri>
   ```
5. commit（commit 在 DI 層；service 內**只 flush**，與既有 `services/audit_service.py` 慣例一致）。

任何一步拋例外 → 整段 rollback，DB 維持調用前狀態。

**embedding API 呼叫順序**：
- 在 INSERT chunks 之前先把 chunks 都計算 embedding（網路呼叫在 txn 內可接受，但要 timeout：每 batch 30s，最多 3 retries）。
- 為避免 txn 開太久：embedding 客戶端**先**全部收齊向量到記憶體（一份 doc 通常 < 500 chunks，沒事），**再**進入 DB INSERT 階段。

---

## 10. Acceptance Criteria

> 每一條對應一個 pytest test function；命名 `test_knowledge_ingestion_ac_NN_*`。

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | Inline markdown happy path | `inline_bytes_b64` 帶 100-line markdown，`scope='funding'` → `is_duplicate=False`，`chunk_count>0`，`embedding_count==chunk_count`，DB 有對應 row。 |
| AC-2 | Duplicate idempotency | 同份 inline 內容跑兩次 → 第二次 `is_duplicate=True`、`chunk_count=0`、`embedding_count=0`、`cost_usd=Decimal("0")`；DB 沒有新增 row。 |
| AC-3 | Sensitive auto-tag | 內文含「身份證 A123456789」→ `is_sensitive=True`、`redactions` 列出 `national_id ≥ 1`、DB 中 `knowledge_chunks.text` 出現 `[REDACTED:national_id]` 不出現原號。 |
| AC-4 | Explicit sensitive override | `explicit_sensitive=True` 且內文無任何 deny-list 命中 → `is_sensitive=True`、`redactions=[]`、DB 文件 `is_sensitive=true`。 |
| AC-5 | Soft-deleted predecessor allows re-ingest | 先 ingest A，將 `knowledge_documents.deleted_at = now()`，再 ingest 同內容 → `is_duplicate=False`（partial unique 排除軟刪除 row），新文件 id 與舊不同。 |
| AC-6 | Chunk boundary by heading | markdown 含 `# A` 兩段 + `# B` 一段，每段 100 tokens、`max_tokens=120` → 至少 3 個 chunk；每個 chunk 的 `section_path` 包含對應標題。 |
| AC-7 | Chunk overlap | `max_tokens=100`, `overlap=20`：相鄰 chunk 文字尾巴與下一個 chunk 文字開頭有 20-token 重疊（容忍 ±2 token 因斷句調整）。 |
| AC-8 | Table not split | markdown table（20 row × 5 col）超過 `max_tokens` → 仍為單一 chunk，meta `has_table=True`。 |
| AC-9 | dry_run does nothing to DB | `dry_run=True` → `embedding_count=0`、`cost_usd=Decimal("0")`、DB 完全不變；但回傳的 `chunk_count` > 0（in-memory 結果）。 |
| AC-10 | Embedding API failure rolls back | mock `EmbeddingClient.embed` 拋 `httpx.ConnectError` 3 次 → service 拋 `IngestionEmbedError`，DB 無新 row（含 audit_log），原檔 sha256 仍未占用。 |
| AC-11 | tenant_id isolation | 同份內容由 tenant A ingest → 由 tenant B ingest → 兩份都成功（`(tenant_id, sha256)` unique，跨 tenant 不衝突）。 |
| AC-12 | Cost calculation matches spec table | 1,000 tokens × `voyage-3-large` → `cost_usd == Decimal("0.000180")`（quantize 到 6dp）。 |
| AC-13 | Markitdown subprocess timeout | inline PDF bytes 模擬 markitdown 卡住 → 60s 後拋 `IngestionExtractError`，DB 無新 row。 |
| AC-14 | Inline size cap | `inline_bytes_b64` decoded > 200 MB → `IngestionDeniedError`，不進 markitdown。 |
| AC-15 | Audit log written | 成功 ingest → `audit_log` 有 1 筆 `action='knowledge.ingested'`、`target_table='knowledge_documents'`、`target_id=<doc.id>`、`after.chunk_count == N`、`after.is_sensitive` 與回傳一致。 |
| AC-16 | Float embed vector rejected | mock `EmbeddingClient.embed` 回傳長度錯（≠ `vector_dim`）→ 拋 `IngestionEmbedError`，DB rollback。 |
| AC-17 | Naive `source_modified_at` rejected | tz-naive datetime → Pydantic `ValidationError`。 |

---

## 11. Edge cases（必須在測試中列舉）

- **空檔**（0 bytes）→ `IngestionExtractError("empty source")`，**不**寫入 0-chunk 文件。
- **內文僅含 deny-list token**（redaction 後 chunk 為空）→ 該 chunk 跳過（不寫 `knowledge_chunks` 空 row）；若整份都被 redact 掉 → 仍寫 `knowledge_documents` (`is_sensitive=True`)，但 `chunk_count=0`、`embedding_count=0`。
- **`source_uri` 同樣 hash 但 `meta` 不同**：以先到的為準，`meta` 不會 merge（idempotency 鍵是 `sha256`，不是 `meta`）。
- **超長行**（單句 > `max_tokens`）：強制硬切；該 chunk meta 寫 `forced_split=True`。
- **DB 寫到一半 embedding API 才回**：不允許（spec 已規定先全收齊向量再進 DB 階段）。
- **markitdown 不存在**：CI 環境必須安裝；測試以 `FakeExtractor` 替換真實 subprocess（dependency injection）。
- **PDF 含掃描頁**：本 spec 不啟用 LLM OCR；該頁文字會被吃成空字串，靠後續人工處理。

---

## 12. Constraints（hard requirements）

- **Python 3.12**，async/await，**不**用 `asyncio.run` 從 sync 進入 async（service 預設由 FastAPI / pytest-asyncio 提供 loop）。
- **依賴**：`pydantic>=2.5`、`sqlalchemy[asyncio]>=2.0`、`httpx>=0.28`、`anthropic>=0.104`（已存在）、`markitdown`（uv-run，與 `scripts/to_md.py` 共存）。**禁止** LangChain / LlamaIndex / pandas / numpy。
- **金錢**：`cost_usd` 用 `Decimal`，6dp，內部不提前 quantize。
- **Time**：所有 timestamp `timezone=True`；naive → 422 / ValidationError。
- **Audit**：寫稽核走 `services.audit_service.audit()`，**不**直接 INSERT `AuditLog`。
- **Logging**：用 `logger.info("event.name", extra={...})`；不准把 `inline_bytes_b64`、`api_key`、`redacted token` 進 log。
- **Errors**：domain 例外用 `api/errors.py` 的 `DomainError` 系列；本 spec 新增 `IngestionDeniedError`、`IngestionExtractError`、`IngestionEmbedError` 三個 subclass。
- **Tests**：用 `tests/conftest.py` 的 `async_session` / `seed_tenant`；不用 sync `TestClient`。
- **No magic numbers**：embedding cost 表、deny-list 規則、size cap、timeout 全是模組層級常數。

---

## 13. Out of scope（重申）

- FastAPI router、HTTP 上送、MCP server tool 定義
- 排程 / 自動同步 Drive / Notion
- Web UI
- 增量更新（同一份原檔小改後重新切 chunk 並 diff embedding）→ 後續 spec
- 跨文件去重的「群集視覺化」
- Embedding 模型 A/B 比較 harness
- Cost 真實扣款 / Stripe 帳單
- 多語自動翻譯
- Streaming ingestion（chunk-by-chunk 邊流邊寫）

---

## 14. Connection to other modules

| Module | 介面 |
|---|---|
| `services.audit_service.audit()` | 每次成功 ingest 寫 1 筆稽核 |
| `models.knowledge.KnowledgeDocument/Chunk` | 主要寫入目標 |
| `models.embeddings.Embedding` | reuse 寫入；`entity_type='knowledge_chunk'`、`entity_id=chunk.id` |
| `api/errors.py` | 新增 3 個 DomainError subclass |
| `scripts/to_md.py` / `make to-md` | 共用 markitdown 工具鏈；本 spec 在 async 內以 subprocess 呼叫，**不**直接 import `to_md.py`（避免耦合到 CLI 腳本） |
| 後續 `routers/knowledge.py` | 將以 `POST /knowledge/ingest` 包裝本 service |
| 後續 `services/knowledge_retrieval.py` | 反向使用本 service 的產物 |

---

## 15. Done = all of:

1. 6 個檔案（`__init__.py` + 5 個模組）就位，type-checks cleanly，ruff 全綠。
2. `tests/services/test_knowledge_ingestion.py` 包含 AC-1 ~ AC-17 對應 test functions。
3. `make full-check` 全綠。
4. `pipeline.py` 頂部 docstring 寫清楚：「為什麼 embedding 收齊再進 DB」與「為什麼 redaction 命中 → 整份文件 `is_sensitive=True` 但仍進 DB」。
5. `DENY_LIST` 變動 → 必須同時更新 `tests/services/test_knowledge_ingestion.py::test_redact_*`。
6. `cost_usd` 計算與 spec §5 的價目表逐字一致；任何模型新增要同時改價目表 + 測試。
7. 一份 `restaurant_api/services/knowledge_ingestion/README.md`（**僅本 spec 允許**新增的 markdown 檔；其他依 CLAUDE.md 規則不主動產文件）內含 30 行內的 quickstart：「如何跑一次 ingestion」。

— end of spec —
