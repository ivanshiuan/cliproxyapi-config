# Spec: Visual Asset Embedding & Semantic Search (`/visual`)

> **Module name:** `restaurant_api.routers.visual_assets`
> **Owner domain:** Marketing / Digital Asset Management
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** FastAPI router mounted into `restaurant_api.main:app`
> **Models touched:** new `restaurant_api/models/visual_assets.py`
> **External weights:** `google/siglip2-so400m-patch16-naflex` (Apache 2.0)
> **Decision context:** `docs/18_vision_encoder_strategy.md`

---

## Background

Phase 2 行銷視覺工作的第一塊基礎建設：把所有上傳的圖片素材（菜色 hero、
品牌 banner、IG 貼文素材、店面照片）轉成可搜尋的向量索引，讓行銷團隊用
**自然語言**或**參考圖**找素材，從「翻 1 小時」變「下 1 個 query」。

技術選型已在 `docs/18_vision_encoder_strategy.md` lock：**SigLIP 2 SO400M**
（400M 參數、Apache 2.0、支援中文、零樣本檢索 2026 SOTA、embedding dim 1152）。
**不**用 RADIO 系列 — 主因是 RADIO 不支援多語（中文對台灣業務剛需）且 NVOML
授權有 Section 8 賠償條款風險。

本 spec 範圍：**單一 router + service + DB migration**，內含模型載入、
embedding 計算、pgvector 索引、語意搜尋四端點。
**不含** S3/MinIO 物件儲存（複用既有 `storage_service`，未來實作）、
**不含** GPU worker 拆分（PoC 階段 in-process 即可，水平擴展時再拆）。

---

## Architecture Decisions

| 決策 | 選擇 | 為什麼 |
|---|---|---|
| 模型載入位置 | **In-process singleton** in FastAPI app | PoC 階段 1 GPU 1 worker 最簡單；scale-out 時再抽到 sidecar |
| 模型精度 | **bf16 on CUDA**, fp32 fallback on CPU（測試用）| SigLIP 2 在 fp16 穩定，bf16 更穩；CPU 跑 fp32 不會 OOM 但慢 |
| Embedding 維度 | **1152**（SO400M backbone embed dim）| 模型固定，動不了 |
| 距離度量 | **cosine** via `vector_cosine_ops` | SigLIP 2 訓練時用 sigmoid logit，cosine 對齊語意檢索 |
| pgvector index | **HNSW**, `m=16, ef_construction=64` | 比 IVFFlat 在中小 corpus（< 1M）recall 更穩 |
| 多租戶隔離 | **scalar filter on tenant_id**, NOT partial index per tenant | 早期 row 量 < 100K，filter 夠快；row > 1M 時改 partitioning |
| 同步 vs 非同步 embedding | **API 內 inline embedding**（< 1 sec on L4）| 不引入 Redis/Celery，PoC 階段過度設計 |
| 模型權重存放 | HuggingFace cache mounted volume `/var/lib/hf-cache` | 不 commit 進 git；首次 pull ~1.6 GB |
| 圖片來源 | **multipart upload OR URL**（兩種都支援） | URL 給後端批次匯入用，multipart 給前端直接上傳 |
| 圖片儲存責任 | **本 spec 不負責**；spec 收到的是「已落地的 URL」或 byte stream | 物件儲存另外抽 service |

### Sequence diagram (POST /visual/assets, multipart path)

```
client            FastAPI            VisualAssetService          GPU(SigLIP 2)         PG
  │  multipart       │                       │                          │                │
  │ ───────────────► │                       │                          │                │
  │                  │  validate mime, size  │                          │                │
  │                  │ ─────────────────────►│                          │                │
  │                  │                       │  PIL decode, resize 384  │                │
  │                  │                       │ ────────────────────────►│                │
  │                  │                       │       embedding (1152)   │                │
  │                  │                       │◄──────────────────────── │                │
  │                  │                       │   INSERT visual_assets   │                │
  │                  │                       │ ──────────────────────────────────────────►│
  │                  │  201 + asset_id       │                          │                │
  │ ◄─────────────── │                       │                          │                │
```

---

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/visual/assets` | 上傳新素材：embed + 入庫 |
| `GET`  | `/visual/assets/{asset_id}` | 讀單筆 |
| `DELETE` | `/visual/assets/{asset_id}` | 軟刪除（`deleted_at`） |
| `POST` | `/visual/search/text` | 中英文 query → top-k 視覺相似素材 |
| `POST` | `/visual/search/image` | 上傳 ref 圖 → top-k 相似素材 |
| `GET`  | `/visual/health` | 模型已載入 + GPU 可用 |

所有路由 prefix：`/visual`；OpenAPI tag：`visual`。
所有路由都注入 `session: AsyncSession = Depends(get_session)` 與
`tenant_id: UUID = Depends(get_tenant_id)`（PoC 階段 `get_tenant_id` 從 header
`X-Tenant-Id` 取，或回 default tenant；Phase 2 換成 JWT claim）。

### POST /visual/assets

接受 `multipart/form-data` **或** `application/json`（後者帶 URL）。

**multipart fields:**

| Field | Type | Required | Validation |
|---|---|---|---|
| `file` | binary | yes (or url) | mime ∈ {`image/jpeg`,`image/png`,`image/webp`}; max 20 MB |
| `kind` | str | yes | `Literal["hero","dish","ingredient","brand_ref","social","ui_ref","other"]` |
| `store_id` | UUID | no | must exist in `stores` if present; null = corporate-level |
| `text_caption` | str | no | ≤ 500 chars，人工標記 |
| `tags` | str (JSON) | no | JSON-encoded `list[str]`, each tag ≤ 50 chars, total ≤ 20 tags |
| `source_label` | str | no | 來源標籤（如 `"line_upload"`、`"web_admin"`），≤ 50 chars |

**JSON body (URL upload):**

```json
{
  "url": "https://storage.example.com/img/abc.jpg",
  "kind": "dish",
  "store_id": null,
  "text_caption": "招牌牛肉麵 hero",
  "tags": ["beef", "noodle", "signature"],
  "source_label": "menu_team"
}
```

URL 必須 HTTPS、回 2xx、`Content-Type` ∈ 上面 whitelist、`Content-Length` ≤ 20 MB。
fetch timeout 10 秒。

**Behaviour:**

1. 驗證輸入。422 if invalid。
2. 取得 image bytes（multipart 直接拿；URL 用 `httpx.AsyncClient` fetch）。
3. PIL `Image.open` decode；若失敗 422 `invalid_image`。
4. resize 到 SigLIP 2 接受的 patch 倍數（384×384 或 naflex 動態，看載入的變體）。
5. 呼叫 `VisualAssetService.embed(image)` → `np.ndarray(shape=(1152,), dtype=float32)`。
6. INSERT `visual_assets` row：
   - `tenant_id`（從 dep）
   - `store_id`（可 null）
   - `kind`
   - `embedding`（pgvector）
   - `source_url`（multipart 時為 null，未來填 S3 path；URL 時填原 URL）
   - `mime_type`、`width`、`height`、`bytes_size`
   - `text_caption`、`tags`、`source_label`
   - `created_by`（從 dep；PoC null 即可）
7. Response 201 `VisualAssetResponse`（**不**回 embedding 內容，太大）。

**Idempotency:**
本 PoC 不做。同一張圖重複上傳會建多筆 row。Phase 2 加 perceptual hash 去重。

### GET /visual/assets/{asset_id}

回單筆 `VisualAssetResponse`；
404 if not found 或 `deleted_at IS NOT NULL` 或 `tenant_id != current`。

### DELETE /visual/assets/{asset_id}

軟刪除（`deleted_at = now()`）。原 row 保留供稽核。
404 if already deleted。
不刪除 pgvector index entry，但 search 端會 filter `deleted_at IS NULL`。

### POST /visual/search/text

**Request:**

```json
{
  "query": "暖色調 熱湯 hero 圖",
  "kind_filter": ["hero", "dish"],
  "store_id_filter": null,
  "tags_filter": [],
  "top_k": 10,
  "min_score": 0.20
}
```

| Field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `query` | str | yes | — | 1..500 chars |
| `kind_filter` | `list[str] \| None` | no | null | each ∈ 同上 kind 列舉 |
| `store_id_filter` | UUID \| None | no | null | must exist if present |
| `tags_filter` | `list[str]` | no | `[]` | AND 邏輯，每 tag ≤ 50 chars |
| `top_k` | int | no | 10 | 1..100 |
| `min_score` | float | no | 0.0 | 0..1（cosine 相似度下限） |

**Behaviour:**

1. 用 SigLIP 2 的 text tower encode `query` → `np.ndarray(shape=(1152,))`。
   text tower 與 image tower 已在訓練時對齊到同空間。
2. 組 SQL：
   ```sql
   SELECT id, kind, text_caption, tags, store_id,
          1 - (embedding <=> :q) AS score
     FROM visual_assets
    WHERE tenant_id = :tenant_id
      AND deleted_at IS NULL
      AND (:kind_filter IS NULL OR kind = ANY(:kind_filter))
      AND (:store_id_filter IS NULL OR store_id = :store_id_filter)
      AND (:tags_filter = ARRAY[]::text[] OR tags ?& :tags_filter)
      AND (1 - (embedding <=> :q)) >= :min_score
    ORDER BY embedding <=> :q
    LIMIT :top_k;
   ```
3. Response 200 `SearchResponse`：

```json
{
  "query": "暖色調 熱湯 hero 圖",
  "results": [
    {"asset_id": "...", "score": 0.74, "kind": "dish", "text_caption": "..."}
  ],
  "latency_ms": 47
}
```

### POST /visual/search/image

`multipart/form-data` with `file`，或 JSON with `url` / `asset_id`。
其中 `asset_id` 表示「找跟這張既有素材最像的」— 直接從 DB 取 embedding，不重算。

| Field | Type | Required | Validation |
|---|---|---|---|
| `file` / `url` / `asset_id` | exactly one of three | yes | 同 POST /visual/assets 規則 |
| `top_k` | int | no (default 10) | 1..100 |
| `kind_filter`, `store_id_filter`, `tags_filter`, `min_score` | 同 text search |  |

Behaviour 同 text search，但 query embedding 從 image tower 來。
**注意**：若用 `asset_id`，從結果中排除該 asset 本身。

### GET /visual/health

```json
{
  "model_loaded": true,
  "model_name": "google/siglip2-so400m-patch16-naflex",
  "device": "cuda:0",
  "dtype": "bfloat16",
  "warm_inference_ms": 23,
  "gpu_mem_used_mb": 1843
}
```

未載入 / 失敗 → 503。

---

## Pydantic Schemas

```python
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

AssetKind = Literal["hero","dish","ingredient","brand_ref","social","ui_ref","other"]

class VisualAssetCreateJSON(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    url: HttpUrl
    kind: AssetKind
    store_id: UUID | None = None
    text_caption: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_label: str | None = Field(default=None, max_length=50)

class VisualAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    store_id: UUID | None
    kind: str
    source_url: HttpUrl | None
    mime_type: str
    width: int
    height: int
    bytes_size: int
    text_caption: str | None
    tags: list[str]
    source_label: str | None
    created_at: datetime   # Asia/Taipei
    updated_at: datetime

class TextSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    query: str = Field(min_length=1, max_length=500)
    kind_filter: list[AssetKind] | None = None
    store_id_filter: UUID | None = None
    tags_filter: list[str] = Field(default_factory=list, max_length=10)
    top_k: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)

class ImageSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    asset_id: UUID | None = None
    url: HttpUrl | None = None
    # file goes via multipart, not this schema
    top_k: int = Field(default=10, ge=1, le=100)
    kind_filter: list[AssetKind] | None = None
    store_id_filter: UUID | None = None
    tags_filter: list[str] = Field(default_factory=list, max_length=10)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)

class SearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    asset_id: UUID
    score: float
    kind: str
    text_caption: str | None
    store_id: UUID | None
    tags: list[str]

class SearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    query: str | None
    results: list[SearchHit]
    latency_ms: int

class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    model_loaded: bool
    model_name: str
    device: str
    dtype: str
    warm_inference_ms: int | None
    gpu_mem_used_mb: int | None
```

---

## Database Schema

### Migration: `alembic/versions/2026_06_27_add_visual_assets.py`

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE visual_assets (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    store_id        UUID REFERENCES stores(id),
    kind            TEXT NOT NULL CHECK (kind IN
                      ('hero','dish','ingredient','brand_ref',
                       'social','ui_ref','other')),
    source_url      TEXT,
    mime_type       TEXT NOT NULL,
    width           INTEGER NOT NULL CHECK (width > 0),
    height          INTEGER NOT NULL CHECK (height > 0),
    bytes_size      BIGINT NOT NULL CHECK (bytes_size > 0),
    embedding       vector(1152) NOT NULL,
    text_caption    TEXT,
    tags            JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_label    TEXT,
    created_by      UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX visual_assets_tenant_active_idx
    ON visual_assets (tenant_id)
    WHERE deleted_at IS NULL;

CREATE INDEX visual_assets_store_idx
    ON visual_assets (store_id)
    WHERE store_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX visual_assets_kind_idx
    ON visual_assets (tenant_id, kind)
    WHERE deleted_at IS NULL;

CREATE INDEX visual_assets_tags_gin_idx
    ON visual_assets USING gin (tags);

-- HNSW for cosine; m=16, ef_construction=64 are pgvector defaults
-- and work well for our expected corpus size (< 1M rows).
CREATE INDEX visual_assets_embedding_hnsw_idx
    ON visual_assets
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE TRIGGER visual_assets_updated_at
    BEFORE UPDATE ON visual_assets
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

### SQLAlchemy model: `restaurant_api/models/visual_assets.py`

```python
from datetime import datetime
from sqlalchemy import (String, BigInteger, Integer, ForeignKey, CheckConstraint,
                        Index, text)
from sqlalchemy.dialects.postgresql import UUID as PgUUID, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from .base import Base, uuid7
from uuid import UUID

class VisualAsset(Base):
    __tablename__ = "visual_assets"
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True,
                                    default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID, ForeignKey("tenants.id"),
                                            nullable=False)
    store_id: Mapped[UUID | None] = mapped_column(PgUUID,
                                                  ForeignKey("stores.id"),
                                                  nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1152), nullable=False)
    text_caption: Mapped[str | None] = mapped_column(String)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_label: Mapped[str | None] = mapped_column(String)
    created_by: Mapped[UUID | None] = mapped_column(PgUUID)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True),
                                                 nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True),
                                                 nullable=False, server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
```

---

## Service Layer

`restaurant_api/services/visual_asset_service.py`

```python
import io
from typing import cast
from PIL import Image
import numpy as np
import torch
from transformers import AutoModel, AutoProcessor
from sqlalchemy.ext.asyncio import AsyncSession

class VisualAssetService:
    """SigLIP 2 image + text encoder singleton.

    Loaded lazily on first use; held as module-global to avoid re-loading
    1.6 GB of weights per request. NOT thread-safe at load time; the
    FastAPI startup hook should call `warmup()` once before serving."""

    _MODEL_ID = "google/siglip2-so400m-patch16-naflex"
    _model = None
    _processor = None
    _device = None
    _dtype = None

    @classmethod
    def warmup(cls) -> None:
        if cls._model is not None:
            return
        cls._device = "cuda" if torch.cuda.is_available() else "cpu"
        cls._dtype = torch.bfloat16 if cls._device == "cuda" else torch.float32
        cls._processor = AutoProcessor.from_pretrained(cls._MODEL_ID)
        cls._model = AutoModel.from_pretrained(
            cls._MODEL_ID, torch_dtype=cls._dtype
        ).to(cls._device).eval()
        # warm forward
        dummy = Image.new("RGB", (384, 384), color=(128, 128, 128))
        cls.embed_image(dummy)

    @classmethod
    @torch.inference_mode()
    def embed_image(cls, img: Image.Image) -> np.ndarray:
        if cls._model is None:
            cls.warmup()
        img = img.convert("RGB")
        inputs = cls._processor(images=img, return_tensors="pt").to(cls._device)
        feats = cls._model.get_image_features(**inputs)
        # L2 normalize for cosine
        feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats[0].float().cpu().numpy()

    @classmethod
    @torch.inference_mode()
    def embed_text(cls, text: str) -> np.ndarray:
        if cls._model is None:
            cls.warmup()
        inputs = cls._processor(
            text=[text], return_tensors="pt", padding="max_length"
        ).to(cls._device)
        feats = cls._model.get_text_features(**inputs)
        feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats[0].float().cpu().numpy()

    @classmethod
    def health(cls) -> dict:
        loaded = cls._model is not None
        return {
            "model_loaded": loaded,
            "model_name": cls._MODEL_ID,
            "device": str(cls._device) if loaded else "n/a",
            "dtype": str(cls._dtype) if loaded else "n/a",
            "gpu_mem_used_mb": (
                torch.cuda.memory_allocated() // (1024 * 1024)
                if loaded and cls._device == "cuda" else None
            ),
        }

# Called from main.py FastAPI lifespan startup
async def warmup_visual_model() -> None:
    VisualAssetService.warmup()
```

**Why singleton, not per-request load**：模型 ~1.6 GB；載入 5-20 秒。
請求中載入會把第一次請求拖到 20 秒以上、後續每次都重新分配 GPU memory。
FastAPI 一個 worker 共享 module state，singleton 是正解。
**前提**：deploy 時 `--workers 1`（GPU 不夠分多 worker），用 nginx/cloudflare
做併發控制；scale-out 用多台機器，不是多 worker。

---

## Database Writes

| Action | Tables written | Notes |
|---|---|---|
| POST /visual/assets | `visual_assets` (1 INSERT) | embedding inline |
| DELETE /visual/assets/{id} | `visual_assets` (UPDATE deleted_at) | 軟刪除 |
| Search | none | read-only |

`embedding` 寫入用 `psycopg` Vector type，**不要**轉成 list/str 寫
（會 round-trip 慢且失精）。

---

## Error Responses

| Status | Trigger | Body |
|---|---|---|
| 400 | malformed body / multipart parse fail | `{"detail": "..."}` |
| 404 | asset_id not found / soft-deleted / tenant mismatch | `{"detail": "asset not found"}` |
| 413 | file > 20 MB | `{"detail": "file too large", "code": "file_too_large"}` |
| 415 | mime 不在 whitelist | `{"detail": "unsupported media type", "code": "unsupported_media"}` |
| 422 | Pydantic validation；URL fetch 失敗；image decode 失敗 | FastAPI 預設格式 |
| 503 | 模型未載入 / CUDA OOM / GPU 不可用 | `{"detail": "model service unavailable"}` |
| 504 | URL fetch timeout (10s) | `{"detail": "source url fetch timeout"}` |

CUDA OOM 處理：catch `torch.cuda.OutOfMemoryError` → `torch.cuda.empty_cache()`
→ 回 503 + alert（log warning level，prod 應接 Sentry）。

---

## Acceptance Criteria

> 每一條對應一個 pytest test function；命名 `test_visual_ac_NN_*`。

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | Health endpoint 上線 | `GET /visual/health` 載入完 200，`model_loaded=true`、`device` 為 `cuda` or `cpu` |
| AC-2 | Embed dim 正確 | `VisualAssetService.embed_image(img).shape == (1152,)` |
| AC-3 | Image L2 normalized | `np.linalg.norm(embed_image(img)) ≈ 1.0 ± 1e-3` |
| AC-4 | Text L2 normalized | `np.linalg.norm(embed_text("hello")).` 同上 |
| AC-5 | Multipart upload 成功 | `POST /visual/assets` with png 201；DB 有 1 row；`embedding` 非 null；維度正確 |
| AC-6 | URL upload 成功 | JSON body with public test URL → 201；fetch + embed + insert 全跑 |
| AC-7 | mime whitelist | 上傳 .gif → 415 |
| AC-8 | file size limit | 上傳 25 MB → 413 |
| AC-9 | corrupted image → 422 | 上傳 random bytes 改副檔名 → 422 `invalid_image` |
| AC-10 | tenant 隔離 read | tenant A 上傳，tenant B `GET /visual/assets/{id}` → 404 |
| AC-11 | tenant 隔離 search | tenant A 上傳，tenant B `POST /visual/search/text` → 不會出現在結果 |
| AC-12 | 軟刪除 search filter | DELETE 後 search 不再回 |
| AC-13 | 中文 query 命中中文素材 | 上傳 3 張湯品圖（caption "招牌牛肉湯"）、3 張甜點（caption "巧克力蛋糕"），query "熱湯"，top-3 都是湯品 row |
| AC-14 | 英文 query 對齊 | query "hot soup" 同上結果 |
| AC-15 | Image search by asset_id | 上傳 2 張視覺相似牛肉麵，`POST /visual/search/image` with asset_id=A → top-1 是另一張且 score > 0.5 |
| AC-16 | Image search 不回 self | with asset_id=A → 結果不含 A |
| AC-17 | top_k 截斷 | 上傳 20 張，`top_k=5` → 5 筆 |
| AC-18 | min_score 過濾 | min_score=0.99 → 大多 query 回空 list |
| AC-19 | kind_filter 工作 | 上傳 dish 跟 hero 各 5 張，`kind_filter=["hero"]` → 只回 hero |
| AC-20 | tags_filter AND 邏輯 | 上傳 tagged `["beef","noodle"]` 跟 `["beef","soup"]`，`tags_filter=["beef","noodle"]` → 只回前者 |
| AC-21 | latency_ms 合理 | search latency_ms < 200（pgvector hnsw + 1 GPU forward） |
| AC-22 | 模型未載入 → 503 | 未呼叫 warmup 直接呼叫 embed → 自動載入；強制 mock unavailable → 503 |
| AC-23 | URL fetch timeout → 504 | mock httpx 模擬 10s+ → 504 |
| AC-24 | 重複上傳允許 | 同圖 POST 兩次 → 兩個 asset_id（不做去重，PoC 限制）|
| AC-25 | retrieval recall 基準 | 用 `tests/fixtures/visual_recall_set/` 1000 張公開測試集（dish 500、scene 300、ui 200），text query 至各 caption → **recall@10 ≥ 0.85** |

---

## Tests

- 檔案位置：
  - `tests/routers/test_visual_router.py`（HTTP 層 + AC 1-24）
  - `tests/services/test_visual_asset_service.py`（service 單元）
  - `tests/integration/test_visual_recall.py`（AC-25，gated by env `RUN_GPU_INTEGRATION_TESTS=1`）

- 框架：`pytest` + `pytest-asyncio` + `httpx.AsyncClient`

- **Model mocking**（單元測試）：
  ```python
  @pytest.fixture
  def mock_siglip(monkeypatch):
      def fake_embed_image(img):
          # deterministic-ish based on image hash
          h = hash(img.tobytes()) % 1000
          rng = np.random.default_rng(h)
          v = rng.normal(size=1152).astype(np.float32)
          return v / np.linalg.norm(v)
      def fake_embed_text(t):
          h = hash(t) % 1000
          rng = np.random.default_rng(h)
          v = rng.normal(size=1152).astype(np.float32)
          return v / np.linalg.norm(v)
      monkeypatch.setattr(VisualAssetService, "embed_image", staticmethod(fake_embed_image))
      monkeypatch.setattr(VisualAssetService, "embed_text", staticmethod(fake_embed_text))
      monkeypatch.setattr(VisualAssetService, "_model", object())  # bypass warmup
  ```

- **GPU integration**（AC-25）：跳過 CI 預設；只在 dev box / nightly 跑。
  Fixture set 用 Pexels/Unsplash CC0 圖，commit 到 git LFS 或從 S3 fetch。

- DB：複用 `tests/conftest.py` 既有 `async_session` + `seeded_tenant` fixture。
  pgvector extension 在 CI image 內已啟用（見 `restaurant_api/docker-compose.yml`）。

- Coverage 目標：所有 AC + happy path + 每個錯誤碼路徑至少 1 test。
  目標總 coverage ≥ 90%（不含整合測）。

---

## Out of Scope

- **Auth / authz**：複用 phase 2 全域認證；本 spec 只接 `get_tenant_id` dep
- **物件儲存**：上傳的 image bytes 不存（PoC）。Phase 2 接 S3/MinIO，本 router
  加 `source_url` 寫入
- **Perceptual hash 去重**：同圖重複上傳會建多筆 row。Phase 2 加 `phash` 欄位
- **背景批次匯入**：CSV / 目錄 mass-import，另開 spec
- **Asset edit**：更新 caption/tags 的 PATCH endpoint，等需求出現
- **Fine-tuning**：本 spec 用 zero-shot SigLIP 2；自家素材 fine-tune 另開 spec
- **GPU worker 分離**：scale-out 才做，本 spec 用 in-process singleton
- **多模型路由**：只 SigLIP 2 一個。`docs/18_vision_encoder_strategy.md` 規劃的
  C-RADIOv4 / Qwen2.5-VL 另開 router
- **觀測性**：embedding latency、cosine 分布、cache hit rate 等 metric 端點，
  Phase 2 接 Prometheus exporter，本 spec 只在 log 寫結構化記錄

---

## Connection to Other Modules

| Module | 介面 |
|---|---|
| `restaurant_api/main.py` | `app.include_router(visual_assets.router, prefix="/visual")`，並在 lifespan startup 呼叫 `await warmup_visual_model()` |
| `restaurant_api/database.py` | 共用 async session；pgvector extension 已在 migration 啟用 |
| `restaurant_api/api/deps.py` | 注入 `session`、`tenant_id`；PoC 從 `X-Tenant-Id` header 取，Phase 2 改 JWT |
| `restaurant_api/api/errors.py` | 用既有 `DomainError`、`NotFoundError`；新增 `ModelUnavailableError`（→ 503）、`SourceFetchError`（→ 504） |
| `restaurant_api/middleware/` | 結構化 log 自動帶 `request_id`、`tenant_id`；search latency 進 `extra={...}` |
| `models/visual_assets.py`（本 spec 新建）| `VisualAsset` 一個 model；不關聯 `orders` / `menu` / `inventory`（純獨立 feature）|
| `docs/18_vision_encoder_strategy.md` | 選型決策來源 |
| `services/audit_service` | 不寫稽核（純素材庫不算敏感操作；DELETE 已軟刪 + 保 row）|

---

## Implementation Plan（3 天）

| Day | 任務 | 驗收 |
|---|---|---|
| 1 上午 | Alembic migration + SQLAlchemy model + pgvector extension 啟用 | `make db-smoke` 跑得通；本機 INSERT/SELECT 一筆 |
| 1 下午 | `VisualAssetService` + warmup + mock fixture | unit test AC 1-4 過 |
| 2 上午 | Pydantic schemas + multipart router + JSON router + GET/DELETE | AC 5-12 過 |
| 2 下午 | Text search + image search endpoints | AC 13-21 過 |
| 3 上午 | Error paths + 503 / 504 / 415 / 413 + 整合測 setup | AC 22-24 過 |
| 3 下午 | 1000 張 fixture set + recall benchmark + PR | AC-25 過、`make full-check` 全綠 |

**Risk register**：

| 風險 | 緩解 |
|---|---|
| GPU 上載入 SigLIP 2 失敗（CUDA 版本不對 / 記憶體不足）| docker-compose 加 `runtime: nvidia`；本機沒 GPU 走 CPU fallback（recall 不變但慢）|
| pgvector HNSW 建索引時 OOM | 先 INSERT 全部 row 再 `CREATE INDEX`；`maintenance_work_mem='1GB'` 暫提高 |
| recall@10 < 0.85（AC-25 不過） | 可調的旋鈕：(a) resize 384 vs 512 vs naflex 動態 (b) bf16 vs fp32 (c) 換 SigLIP 2 g 變體（更大）|
| HF 拉權重慢 / 失敗 | volume mount HF cache；CI 用 mock，dev 預先 `huggingface-cli download` |

---

— end of spec —
