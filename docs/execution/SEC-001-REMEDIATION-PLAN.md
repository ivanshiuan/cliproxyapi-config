# SEC-001 — Remediation Plan

**Issue:** SEC-001 Unauthorized Asset Read  
**Severity:** P1  
**Status:** SPEC COMPLETE — NOT IMPLEMENTED  
**Last Updated:** 2026-07-29  
**Product Code Modified:** NO  
**Commit Created:** NO  
**Push Status:** NOT PUSHED  
**Deployment Status:** NOT DEPLOYED

---

## Section 1 — Target Security Model

**Declared Target:** `PROJECT-SCOPED PRIVATE ASSET`

Every asset in LP_CODE mode must satisfy all of the following attributes:

| Attribute | Required | Notes |
|---|---|---|
| Stable Asset ID | YES | Immutable identifier for the asset; survives URL scheme changes |
| Canonical Asset Key | YES | R2 object key; must include project scope prefix |
| Project ID | YES | Logical scope boundary; isolates assets between projects |
| Optional Owner / Tenant ID | OPTIONAL | For future multi-tenant; not required in current single-LP_CODE mode |
| Content-Type | YES | Stored in R2 object metadata; used for safe response headers |
| Created At | YES | Stored in R2 object metadata |
| Optional Version | OPTIONAL | For generative re-runs producing updated versions of same asset |
| Authorization Scope | YES | Which projects / LP_CODEs may access this asset |

---

## Section 2 — Data Model Readiness Assessment

The following 10 questions must be answered by inspecting the actual codebase before Phase 2
can be authorized. **Current status: UNKNOWN (codebase not accessible).**

| # | Question | Status | Answer |
|---|---|---|---|
| 1 | Does existing Asset Metadata store Project ID? | UNKNOWN | Requires inspection of R2 object metadata schema and any KV/D1 tables |
| 2 | Does R2 Object Key include Project Scope? | UNKNOWN | Requires inspection of key-generation logic in `functions/_lib.js` |
| 3 | If no Project ID: backward-compatible Migration? | UNKNOWN | Cannot design migration until (1) and (2) are answered |
| 4 | How are old assets classified and handled? | UNKNOWN | Depends on whether a migration is possible |
| 5 | Single-user vs. future multi-tenant compatibility? | UNKNOWN | LP_CODE currently implies single-project; multi-tenant design requires Founder decision |
| 6 | Canvas State: store Asset ID, Object Key, or URL? | UNKNOWN | Must store **Stable Asset Reference** (Asset ID or scoped Object Key), never a Signed URL |
| 7 | How to refresh a Signed URL after expiry? | DEFINED | Client calls `GET /api/assets/{asset_key}/url?project={pid}` with valid LP_CODE session |
| 8 | Project Delete → revoke Asset access? | UNKNOWN | Requires decision on whether to delete R2 objects, set access=revoked in metadata, or rely on project_id fence |
| 9 | Export needs extra permissions? | UNKNOWN | Requires Founder decision on whether export is a separate access tier |
| 10 | Share feature with Private Asset isolation? | UNKNOWN | Requires Founder decision on share model: share URL → temporary public? Or share requires invitee auth? |

**Data Model Readiness: UNKNOWN**

> Until questions (1)–(3) are answered by codebase inspection, Phase 2 is BLOCKED.
> Phase 1 does NOT require data model changes.

---

## Section 3 — Signing Secret Design

### 3.1 Secret Identity

| Property | Value |
|---|---|
| Secret Name | `ASSET_SIGNING_SECRET` |
| Type | High-entropy random bytes, hex-encoded |
| Minimum entropy | 256 bits (32 random bytes → 64 hex chars) |
| Generation | `openssl rand -hex 32` (run locally; never committed) |

### 3.2 Storage

| Environment | Storage Method |
|---|---|
| Cloudflare Production | `wrangler secret put ASSET_SIGNING_SECRET` — stored in Cloudflare encrypted secret store, injected as env var at runtime, never appears in source code or wrangler.toml |
| Cloudflare Preview | Separate secret per preview environment — `wrangler secret put ASSET_SIGNING_SECRET --env preview` |
| Local Development | `.dev.vars` file (gitignored by Cloudflare Pages convention) — `ASSET_SIGNING_SECRET=<local-value>` |
| Git | **NEVER** — not in `wrangler.toml`, not in `.env`, not in any committed file |
| Frontend | **NEVER** — secret only lives in Cloudflare Worker runtime (server-side only) |
| Logs | **NEVER** — code must not log secret or any signature value |

### 3.3 Key Versioning

Every signing operation embeds a **Key ID (`kid`)** so that multiple keys can coexist during
rotation.

| Property | Value |
|---|---|
| Format | Integer version string, e.g., `"v1"`, `"v2"` |
| Storage for current kid | Env var `ASSET_SIGNING_KID` (default `"v1"`) |
| Multiple active kids | `ASSET_SIGNING_SECRET_v1`, `ASSET_SIGNING_SECRET_v2` — worker reads both during grace period |

### 3.4 Secret Rotation

```
Step 1: Generate new secret locally  →  openssl rand -hex 32
Step 2: Push new secret to Cloudflare under new kid (e.g., v2)
         wrangler secret put ASSET_SIGNING_SECRET_v2
Step 3: Update ASSET_SIGNING_KID to "v2" (new tokens use v2)
Step 4: Verification: run acceptance tests against staging
Step 5: Grace period (default 24 hours) — worker accepts both v1 and v2
Step 6: After grace period: remove ASSET_SIGNING_SECRET_v1 from Cloudflare
Step 7: Worker no longer accepts v1 tokens
```

### 3.5 Missing Secret — Fail-Closed Behavior

If `ASSET_SIGNING_SECRET` (or the active kid's secret) is missing at request time:

```
→ Reject ALL signed URL verification requests with HTTP 503
→ Log: "ASSET_SIGNING_SECRET missing — asset delivery disabled"
→ Do NOT fall back to unsigned delivery
→ Do NOT expose error detail to client (return generic 503)
```

### 3.6 Prohibited Uses of LP_CODE

LP_CODE is a user-facing access code. It:

- **MUST NOT** be used as HMAC secret
- **MUST NOT** be used to derive HMAC secret via any KDF
- **MUST NOT** appear in HMAC inputs (it is not stable, not high-entropy)
- **MUST NOT** be transmitted to Cloudflare secrets store as ASSET_SIGNING_SECRET

The separation is absolute: LP_CODE is an **authentication credential**; ASSET_SIGNING_SECRET
is a **cryptographic signing key**. These serve different purposes and must never be conflated.

---

## Section 4 — Signed Token Payload

### 4.1 Payload Structure

Each signed token encodes the following JSON payload (base64url-encoded, no padding):

```json
{
  "v": 1,
  "kid": "v1",
  "method": "GET",
  "key": "<canonical_asset_key>",
  "pid": "<project_id>",
  "exp": 1722000000
}
```

| Field | Type | Description |
|---|---|---|
| `v` | integer | Payload schema version (currently `1`) |
| `kid` | string | Key ID corresponding to the signing secret used |
| `method` | string | HTTP method; currently always `"GET"` |
| `key` | string | Canonical asset key (see §4.2) |
| `pid` | string | Project ID; must match the project that owns the asset |
| `exp` | integer | Unix timestamp (seconds UTC) of expiry |

### 4.2 Canonical Asset Key Rules

The canonical asset key is computed before signing and before storage.

1. **Lowercase:** all characters converted to lowercase
2. **No leading slash:** remove any leading `/`
3. **No trailing slash:** remove any trailing `/`
4. **Separator:** path components joined by forward slash `/`
5. **No query parameters:** strip all `?...` from the key
6. **No fragment:** strip all `#...`
7. **Percent-encoding:** decode percent-encoded characters, then re-encode non-safe characters using RFC 3986 unreserved character set
8. **Path normalization:** collapse `./` and `../` sequences; reject keys with `..` after normalization

Example:
```
Input:   /Projects/PROJ-001/assets/img/../img/result_20260729.jpg?v=1
Canonical: projects/proj-001/assets/img/result_20260729.jpg
```

### 4.3 Canonical String for HMAC

The string signed by HMAC-SHA256 is constructed as:

```
{v}\n{method}\n{canonical_key}\n{pid}\n{exp}\n
```

Example:
```
1\nGET\nprojects/proj-001/assets/img/result_20260729.jpg\nproj-001\n1722000000\n
```

Rules:
- Fields separated by literal newline `\n` (LF, U+000A)
- Trailing newline after last field
- UTF-8 encoding
- No extra whitespace

### 4.4 Token Format (URL Query Parameter)

```
?token={PAYLOAD_B64}.{SIG_B64}
```

Where:
- `PAYLOAD_B64` = base64url (no padding) of JSON payload (UTF-8)
- `SIG_B64` = base64url (no padding) of HMAC-SHA256 raw bytes

Full example URL:
```
/api/assets/projects/proj-001/assets/img/result_20260729.jpg
  ?token=eyJ2IjoxLCJraWQiOiJ2MSIsIm1ldGhvZCI6IkdFVCIsImtleSI6InByb2plY3RzL3Byb2otMDAxL2Fzc2V0cy9pbWcvcmVzdWx0XzIwMjYwNzI5LmpwZyIsInBpZCI6InByb2otMDAxIiwiZXhwIjoxNzIyMDAwMDAwfQ.bWFjX3NpZ25hdHVyZV9oZXJl
```

### 4.5 Fourteen Verification Requirements

| # | Requirement | Failure Response |
|---|---|---|
| 1 | Canonicalization of incoming request path | 400 Bad Request |
| 2 | URL decoding before path comparison | 400 Bad Request |
| 3 | Path normalization (reject `..`) | 400 Bad Request |
| 4 | Query parameter order: `token` extracted first, then stripped from canonical key | 400 Bad Request |
| 5 | HMAC algorithm: SHA-256 only | 500 (misconfiguration) |
| 6 | Signature encoding: base64url, no padding | 400 Bad Request |
| 7 | **Constant-time comparison:** use `crypto.subtle` `timingSafeEqual` or equivalent; NEVER `===` string compare | 403 Forbidden |
| 8 | Clock skew: accept tokens with `exp` up to 30 seconds in the past (accommodates clock drift) | 401 Unauthorized (expired) |
| 9 | Maximum TTL: reject tokens where `exp - iat > 3600` (1 hour max) — requires `iat` field in payload | 400 Bad Request |
| 10 | Expired token response: `HTTP 401` with body `{"error": "token_expired"}` (no detail) | 401 Unauthorized |
| 11 | Invalid signature response: `HTTP 403` with body `{"error": "forbidden"}` (no detail, no hint) | 403 Forbidden |
| 12 | Wrong asset key response: `HTTP 403` — `key` in payload does not match canonical request path | 403 Forbidden |
| 13 | Wrong project scope response: `HTTP 403` — `pid` in payload does not match asset's owning project | 403 Forbidden |
| 14 | Unknown key version response: `HTTP 403` — `kid` in payload is not a recognized active key | 403 Forbidden |

**Token-to-Asset binding:** a signed token for asset key `A` MUST NOT authorize access to
asset key `B`. The `key` field in the payload is compared to the canonical request path
after independent canonicalization of the request. Mismatch → 403.

---

## Section 5 — URL Signing Flow

### 5.1 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  FRONTEND (Cloudflare Pages, browser)                                   │
│                                                                         │
│  Canvas loads / component mounts                                        │
│  → reads Stable Asset Reference from persisted Canvas State            │
│    { "asset_key": "projects/proj-001/assets/img/xxx.jpg",              │
│      "project_id": "proj-001" }                                        │
│  → sends  GET /api/assets/{asset_key}/url?project={pid}                │
│    with LP_CODE session cookie / header                                 │
└──────────────────┬──────────────────────────────────────────────────────┘
                   │ HTTPS
┌──────────────────▼──────────────────────────────────────────────────────┐
│  CLOUDFLARE PAGES FUNCTION  (/api/assets/*/url)                         │
│                                                                         │
│  1. Verify LP_CODE / session — reject if invalid → 401                 │
│  2. Verify caller has access to project_id — reject if not → 403       │
│  3. Verify asset exists and belongs to project_id — reject if not → 404│
│  4. Canonicalize asset_key                                              │
│  5. Generate payload: { v, kid, method, key, pid, exp=now+TTL }        │
│  6. Compute canonical string                                            │
│  7. HMAC-SHA256(secret[kid], canonical_string) → sig                   │
│  8. Return { signed_url: "/api/assets/{key}?token={payload}.{sig}",    │
│              expires_at: {exp_iso} }                                    │
└──────────────────┬──────────────────────────────────────────────────────┘
                   │ signed_url returned to frontend
┌──────────────────▼──────────────────────────────────────────────────────┐
│  FRONTEND loads asset via signed_url                                    │
│  → GET /api/assets/{asset_key}?token={...}                             │
└──────────────────┬──────────────────────────────────────────────────────┘
                   │ HTTPS
┌──────────────────▼──────────────────────────────────────────────────────┐
│  CLOUDFLARE PAGES FUNCTION  (/api/assets/*)                             │
│                                                                         │
│  1. Extract token from query param                                      │
│  2. Decode and parse payload                                            │
│  3. Verify kid → load correct secret                                    │
│  4. Recompute canonical string from payload fields                      │
│  5. HMAC-SHA256 verify (constant-time)                                  │
│  6. Check exp ≤ now + 30s clock skew                                    │
│  7. Check canonical(request.path) == payload.key                        │
│  8. Check asset.project_id == payload.pid                              │
│  9. Fetch from R2: env.ASSETS.get(canonical_key)                        │
│ 10. Return with headers:                                                │
│     Cache-Control: private, no-store                                    │
│     X-Content-Type-Options: nosniff                                     │
│     Content-Type: {from R2 metadata}                                    │
│     Content-Disposition: inline                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Signing Flows by Use Case

| Use Case | Signing Endpoint | TTL | Notes |
|---|---|---|---|
| Canvas initial load | `GET /api/assets/{key}/url` per asset | 3600s | Batch: `POST /api/assets/urls` with array of keys |
| Canvas refresh (page reload) | Same endpoint | 3600s | Client re-requests on 401 from asset endpoint |
| Signed URL expiry (within session) | Client catches 401 from asset endpoint → re-requests URL | 3600s | No user interaction needed |
| Thumbnail | `GET /api/assets/{key}/url?type=thumbnail` | 3600s | Same mechanism; smaller key prefix |
| Generation result (new AI image) | Signing endpoint called after upload confirmation | 3600s | Asset must be stored before signing |
| Upload preview | Pre-signed upload URL (separate mechanism, PUT method) | 300s | Scope to upload only; upload URL ≠ read URL |
| Download (user-initiated) | `GET /api/assets/{key}/url?disposition=attachment` | 300s | Response includes `Content-Disposition: attachment` |
| Export | `POST /api/export` returns signed URLs for all included assets | 3600s | Requires export permission check (see §2 Q9) |
| Undo / Redo | Stable Asset Reference in history stack; re-sign on access | 3600s | History stores keys not URLs |
| Project Recovery | Scan asset keys by project_id prefix in R2; re-sign each | 3600s | Admin operation |

### 5.3 Batch Signing Endpoint

For canvas with many assets (thumbnails, layers):

```
POST /api/assets/urls
Content-Type: application/json
LP_CODE: <session>

{
  "project_id": "proj-001",
  "assets": [
    { "key": "projects/proj-001/assets/img/xxx.jpg" },
    { "key": "projects/proj-001/assets/thumb/xxx_t.jpg" }
  ]
}

→ 200 OK
{
  "urls": [
    { "key": "...", "url": "...", "expires_at": "..." },
    { "key": "...", "url": "...", "expires_at": "..." }
  ]
}
```

Maximum 50 assets per batch request.

### 5.4 Canvas Persistence — Stable Asset Reference

Canvas state stored in KV, D1, or R2 metadata MUST NOT contain Signed URLs.

**PROHIBITED (current suspected state):**
```json
{
  "type": "image",
  "src": "/api/assets/abc123.jpg?token=eyJ...&exp=1722000000"
}
```

**REQUIRED (after Phase 2 migration):**
```json
{
  "type": "asset_ref",
  "asset_key": "projects/proj-001/assets/img/abc123.jpg",
  "project_id": "proj-001",
  "content_type": "image/jpeg",
  "width": 1024,
  "height": 1024
}
```

The frontend resolves Stable Asset References to signed URLs at load time. On expiry,
it catches HTTP 401 from the asset endpoint and calls the sign endpoint again automatically.

---

## Section 6 — Cache Policy

### 6.1 LP_CODE Mode (Private Mode) — Default

All asset responses in LP_CODE mode MUST include:

```
Cache-Control: private, no-store
X-Content-Type-Options: nosniff
Content-Type: <exact type from R2 metadata, not guessed>
Content-Disposition: inline
```

**Rationale:**
- `private` prevents shared/CDN cache
- `no-store` prevents browser cache persistence
- `no-store` means no cache file is written; the previous `Cache-Control: public` header
  must be removed and replaced, not just added to
- Signed URLs with `no-store` have no risk of cache-bypass attacks

### 6.2 Public Mode (LP_CODE not enabled)

Public mode MUST NOT be the default. It requires an **explicit Founder decision** and code
change to activate. If activated:

```
Cache-Control: public, max-age=3600, stale-while-revalidate=86400
X-Content-Type-Options: nosniff
```

Public mode assets MUST be stored under a separate R2 key prefix (e.g., `public/`) to
prevent conflation with private assets. This is a product decision, not a security default.

### 6.3 Handling Existing Cache After Remediation

The remediation changes the response headers. This does NOT automatically clear existing
Cloudflare CDN cache or browser cache. The following steps are required:

#### 6.3.1 Cloudflare CDN Purge

After deploying Phase 1:

```
Option A — Purge by URL (preferred if URL set is known):
  POST https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache
  { "files": ["https://your-pages.dev/api/assets/known-url-1", ...] }

Option B — Purge Everything (use if URL set is not known):
  POST https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache
  { "purge_everything": true }
  WARNING: This purges ALL cached content for the zone, not just assets.
```

A "Purge Everything" must be executed immediately after Phase 1 deployment. Cloudflare
Zone ID is required — verify with `wrangler` or Cloudflare dashboard.

#### 6.3.2 Browser Cache

Browser cache (`no-store` in prior responses) cannot be force-cleared by the server after
the fact. Mitigation strategies:

- **URL Versioning:** append a version suffix to asset URLs (e.g., `?v=2`) to create a new
  cache key that bypasses the old cached entry. Requires updating all stored asset references.
- **Accept expiry:** browsers typically respect `no-store` on next navigation; cached content
  expires per the previous `max-age` value.
- Old URL validity: old unsigned URLs (pre-Phase 2) will be invalidated by Phase 2 because
  the asset endpoint will require a valid signed token. Old URLs without a token → 401.

#### 6.3.3 Verification Steps After Purge

| Step | Command | Expected Result |
|---|---|---|
| Check CDN cache status | `curl -I https://{pages-url}/api/assets/{key}` | `CF-Cache-Status: MISS` or `BYPASS` |
| Check Age header | Same | `Age: 0` or absent |
| Anonymous asset request | `curl -I https://{pages-url}/api/assets/{key}` (no LP_CODE) | `HTTP 401` (after Phase 2) or `HTTP 403` |
| Cache-Control header | Same | `Cache-Control: private, no-store` |
| Browser new private window | Navigate to asset URL | `HTTP 401` or `HTTP 403`, no image displayed |

#### 6.3.4 Query String Cache Key Warning

If Cloudflare cache rules use query strings as part of the cache key, the signed `?token=`
parameter could create unbounded cache entries (one per unique signed URL). Since Phase 1
uses `Cache-Control: private, no-store`, this is prevented — Cloudflare will not cache
`no-store` responses. Verify that no Cloudflare cache rule overrides `no-store`.

---

## Section 7 — Phase 1: Immediate Mitigation

**Goal:** Stop unauthorized cache-based access and add LP_CODE guard to asset endpoint.  
**Status:** READY (does not require data model changes)  
**Estimated scope:** 1–2 files modified in `functions/_lib.js` (or equivalent asset handler)

### 7.1 Changes Required

| Change | File | Description |
|---|---|---|
| Add LP_CODE auth guard | `functions/_lib.js` | Check LP_CODE / session on all `/api/assets/*` routes before R2 fetch |
| Change Cache-Control | `functions/_lib.js` | Replace `Cache-Control: public, max-age=...` with `Cache-Control: private, no-store` |
| Add security headers | `functions/_lib.js` | Add `X-Content-Type-Options: nosniff` to all asset responses |
| Fix Content-Type | `functions/_lib.js` | Set Content-Type from R2 object metadata, not inferred from URL extension |

### 7.2 Phase 1 Status Declaration

After Phase 1 implementation and all Phase 1 tests pass:

**SEC-001 status: `MITIGATED — NOT CLOSED`**

Remaining risks:
- No project-scope enforcement (any LP_CODE holder can access any asset)
- No signed URL expiry (URL leaked from browser history still works until Phase 2)
- Canvas still stores URLs (or keys without scope) — functional but not fully secure
- Old cached responses may still exist in browsers until expiry or URL versioning

### 7.3 Phase 1 Rollback

Rollback to pre-Phase-1 state:
- Revert `functions/_lib.js` to previous version
- Re-deploy to Cloudflare Pages
- Note: rollback re-opens SEC-001; document rollback in ISSUE_REGISTRY.json

### 7.4 Phase 1 Regression Risk

| Risk | Probability | Mitigation |
|---|---|---|
| Canvas images stop loading | MEDIUM (if auth guard incorrectly blocks signed requests) | Canvas regression test suite (§8.4) |
| Thumbnails stop loading | MEDIUM | Include thumbnails in regression tests |
| Export broken | LOW | Export test in Phase 1 test plan |
| Upload preview broken | LOW | Upload flow test in Phase 1 test plan |

---

## Section 8 — Phase 2: Complete Signed URL

**Goal:** Full project-scoped private asset delivery with signed URLs, Canvas reference migration.  
**Status:** BLOCKED — pending data model readiness assessment  
**Prerequisite:** Answers to Section 2 questions (1)–(5) by codebase inspection

### 8.1 Phase 2 Work Items

| Item | Description | Blocked By |
|---|---|---|
| R2 key schema | Add project scope prefix to R2 object keys: `projects/{pid}/{type}/{uuid}.{ext}` | Codebase inspection (Q2) |
| Metadata schema | Add `project_id`, `asset_id`, `asset_type`, `created_at` to R2 object metadata | Codebase inspection (Q1) |
| Signing endpoint | `GET /api/assets/{key}/url` and `POST /api/assets/urls` | Phase 1 complete |
| Verification middleware | Token verification in asset handler (§4.5) | R2 key schema decision |
| Canvas reference migration | Update Canvas persistence to store Stable Asset Reference (§5.4) | Codebase inspection (Q6) |
| URL refresh logic | Client catches 401 and re-fetches signed URL | Canvas migration complete |
| Old asset migration | Script to re-key existing R2 objects to scoped prefix | R2 key schema decision |
| Secret provisioning | `wrangler secret put ASSET_SIGNING_SECRET` in all environments | Cloudflare account access |
| Export integration | Update export to sign all included assets | Phase 1 + signing endpoint |
| Project scope enforcement | Verify `pid` in token matches asset's owning project | Metadata schema complete |
| Secret rotation procedure | Document + test (§3.4) | Signing secret design complete |
| Full test suite | §8 test specifications | All above |

### 8.2 Phase 2 Rollback

Phase 2 introduces data migration (R2 key rename). Rollback requires:
- Maintaining old R2 keys during transition (copy, not move)
- Reverting Canvas state reference format
- Reverting signing endpoint
- Purging CDN cache again

---

## Section 9 — Test Specifications

### 9.1 Signature Unit Tests

| Test | Input | Expected |
|---|---|---|
| valid_token | Correct payload + correct signature + not expired | HTTP 200, asset returned |
| expired_token | Correct payload + correct signature + exp=now-60 | HTTP 401, `{"error":"token_expired"}` |
| future_exp_beyond_max_ttl | exp = now + 7200 (> 3600s max) | HTTP 400, rejected |
| wrong_signature | Correct payload + 1-byte-flipped signature | HTTP 403, `{"error":"forbidden"}` |
| wrong_asset_key | token.key="A" but request path is "/B" | HTTP 403, `{"error":"forbidden"}` |
| wrong_project | token.pid="proj-001" but asset belongs to "proj-002" | HTTP 403, `{"error":"forbidden"}` |
| wrong_method | token.method="POST" but request is GET | HTTP 403 |
| unknown_kid | token.kid="v99" (not a known key) | HTTP 403, `{"error":"forbidden"}` |
| modified_query | Append extra query param to signed URL | HTTP 403 (canonical changes) |
| encoding_variant_1 | URL path with %2F instead of / | Canonicalized → same result as unencoded |
| encoding_variant_2 | Uppercase path letters | Canonicalized to lowercase → same result |
| timing_safe | Measure time for valid vs invalid sig | Times must not be distinguishable |

### 9.2 Authorization Tests

| Test | Description | Expected |
|---|---|---|
| correct_project_access | Requester has LP_CODE for proj-001, asset belongs to proj-001 | HTTP 200 |
| wrong_project_access | Requester has LP_CODE for proj-001, asset belongs to proj-002 | HTTP 403 |
| missing_session | No LP_CODE / session cookie | HTTP 401 |
| invalid_lp_code | LP_CODE provided but incorrect | HTTP 401 |
| asset_other_project | Valid session, but signed URL is for another project's asset | HTTP 403 |
| deleted_project | Asset's project has been deleted | HTTP 403 or HTTP 404 (per Founder decision) |
| deleted_asset | Asset has been deleted from R2 | HTTP 404 |

### 9.3 Cache Tests

| Test | Description | Expected |
|---|---|---|
| private_mode_no_public_cache | LP_CODE mode asset response | `Cache-Control: private, no-store` |
| public_mode_only_explicit | Public mode asset response | `Cache-Control: public, max-age=3600` (only if explicitly enabled) |
| no_cdn_cache_hit | Request asset twice; check CF-Cache-Status | `CF-Cache-Status: BYPASS` or `MISS`, never `HIT` |
| query_string_bypass | Append arbitrary query params to asset URL | Still requires valid token; no cache bypass |
| token_query_no_cache_split | Two requests with different tokens for same asset | Both get `BYPASS`; token not used as CDN cache key differentiator |
| purge_verification | Purge specific URL via Cloudflare API, then fetch | `CF-Cache-Status: MISS`; fresh origin request |
| expired_url_no_cache | Request with expired token | HTTP 401; not cached by CDN |

### 9.4 Canvas Regression Tests (Phase 1 and Phase 2)

| Test | Expected Result |
|---|---|
| existing_project_loads | Canvas renders without errors |
| images_appear | All canvas image assets display |
| thumbnail_appears | Project thumbnail renders in gallery |
| new_generation_appears | New AI-generated image appears in canvas after generation |
| refresh_restores_image | Browser reload → canvas images re-appear |
| expired_url_is_refreshed | Simulate signed URL expiry (wait or manipulate) → client re-fetches URL automatically → image reloads |
| undo_redo_works | Undo/Redo operations restore images correctly |
| download_works | User-initiated download completes and file is correct |
| export_works | Project export produces correct output with all images |
| deleted_asset_fails_safely | Canvas shows placeholder/error for deleted asset; does not crash |

### 9.5 Production Verification (Plan Only — Not Executed This Round)

These tests are planned for the first authorized production deployment and are **NOT to be
executed until Phase 1 or Phase 2 is deployed to a non-production Cloudflare environment**.

| Test | Command | Expected |
|---|---|---|
| anon_known_url | `curl -I https://{pages}.dev/api/assets/{known_key}` (no auth) | HTTP 401 (Phase 1) or HTTP 401 (Phase 2) |
| valid_signed_request | `curl "https://{pages}.dev/api/assets/{key}?token={valid}"` | HTTP 200, asset bytes |
| expired_signed_request | `curl "https://{pages}.dev/api/assets/{key}?token={expired}"` | HTTP 401 |
| cf_cache_status | `curl -I {asset_url}` | `CF-Cache-Status: BYPASS` or `MISS` |
| age_header | Same | `Age: 0` or absent |
| browser_reload | Open asset URL in browser, reload | HTTP 401 (no signed URL from browser address bar) |
| new_private_window | Open asset URL in new private window | HTTP 401 |
| cache_purge_verification | Purge URL, then fetch | `CF-Cache-Status: MISS` |
