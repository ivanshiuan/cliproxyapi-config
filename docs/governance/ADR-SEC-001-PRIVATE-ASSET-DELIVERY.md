# ADR-SEC-001 — Private Asset Delivery Architecture

**Status:** ACCEPTED (Specification)  
**Date:** 2026-07-29  
**Deciders:** Ivan (Founder) — pending review  
**Security Issue:** SEC-001 Unauthorized Asset Read (P1)

---

## Context

The lovart-plus project uses Cloudflare R2 to store user-uploaded assets and AI-generated
images (CogView / Zhipu). The application is protected by LP_CODE (a passcode-based access
control). The asset-serving endpoint currently does not verify LP_CODE before serving R2
objects, meaning any party who knows or can guess an R2 object key can retrieve assets
without authentication.

Additionally, the asset response sets `Cache-Control: public`, which allows Cloudflare CDN
and browsers to cache asset responses. If CDN caches an asset response, subsequent
anonymous requests may be served the cached asset without contacting the origin — bypassing
even a fixed auth guard.

This ADR decides how to deliver private assets securely in this architecture.

**Key constraints:**
- Runtime: Cloudflare Pages Functions (Workers runtime)
- Storage: Cloudflare R2
- Auth model: LP_CODE (single-project passcode, not per-user session tokens today)
- Frontend: Canvas with many image assets loaded per page
- AI generation: CogView images downloaded from `ufileos.com` and stored in R2
- No database currently in use for asset metadata (UNKNOWN — may use R2 object metadata only)

---

## Decision Drivers

1. **P1 security vulnerability:** unauthorized read of private assets is confirmed locally
2. **Secret hygiene:** LP_CODE must not be used as a cryptographic signing key
3. **CDN cache isolation:** private assets must not be cached by shared CDN infrastructure
4. **Canvas usability:** the solution must not break canvas image loading or cause
   unacceptable latency (batch signing endpoint addresses this)
5. **Stable references:** canvas state must survive URL scheme changes without data migration
6. **Operational simplicity:** the team is small; the solution must be operationally maintainable

---

## Alternatives Considered

### Option 1 — DIRECT AUTH (Authentication per request, no signed URL)

Every R2 asset request goes through the Cloudflare Pages Function which verifies LP_CODE on
every request before fetching from R2 and streaming the response.

**Pros:**
- Simple: no token generation or verification logic
- Immediate revocation (LP_CODE change takes effect immediately)
- No token rotation needed

**Cons:**
- Every image load goes through the Function runtime (no CDN caching possible)
- Higher latency for canvas with many images
- Does not allow `Content-Disposition: attachment` download URLs to be sent in emails
  or external systems (they'd need LP_CODE)
- No project-scope enforcement without metadata lookup on every request
- Cannot support future share-link use cases cleanly

**Decision:** Not selected as primary. May be used as Phase 1 interim (simpler to implement,
closes the unauthorized access immediately) before Phase 2 Signed URL.

### Option 2 — AUTHENTICATED PROXY (server proxies R2, auth checked per request)

A Cloudflare Pages Function acts as a proxy: it verifies LP_CODE, fetches the R2 object,
and streams it to the client. Same as Option 1 but described as a proxy pattern.

This is functionally identical to Option 1 in the Cloudflare Pages / R2 context. Not
separately evaluated.

### Option 3 — SIGNED URL (short-lived scoped URL with HMAC token)

The frontend requests a short-lived signed URL from a backend endpoint (which verifies
LP_CODE). The signed URL encodes the asset key, project scope, expiry, HTTP method, and
key version in a HMAC-SHA256 token. The asset endpoint verifies the token independently
without checking LP_CODE on the asset request itself.

**Pros:**
- Token is time-limited (reduces window of exposure if URL is leaked)
- Token is asset-scoped (cannot be replayed against a different asset)
- Token is project-scoped (enforces project boundary even if key is guessed)
- Allows `Cache-Control: private` at asset endpoint with no-store (CDN bypass)
- Enables future per-asset permissions without changing URL structure
- Stable Asset References in canvas state are URL-scheme-independent
- Supports share-link patterns (generate a longer-TTL token for shared access)

**Cons:**
- More complex implementation (signing endpoint + verification middleware)
- Requires ASSET_SIGNING_SECRET management (rotation, storage)
- Canvas must implement token refresh on expiry (401 → re-sign)
- Requires data model changes for Phase 2 (project-scoped R2 keys)

**Decision:** Selected as the target architecture (Phase 2). Phase 1 uses DIRECT AUTH as
interim until Signed URL is implemented.

### Option 4 — R2 Presigned URLs (native R2 mechanism)

Cloudflare R2 supports presigned URLs (similar to S3 presigned URLs) that bypass the
Worker and go directly to R2. These cannot enforce application-level project scope or
LP_CODE. They require Worker-side generation with the R2 binding and have a maximum
validity of 7 days. They do not support project isolation without custom logic.

**Decision:** Not selected. Direct R2 presigned URLs bypass all application-level access
controls and cannot enforce LP_CODE or project scope.

---

## Decision

**Phase 1 (Immediate):** Use DIRECT AUTH (Option 1) to close SEC-001 immediately:
- Add LP_CODE guard to asset endpoint
- Change `Cache-Control` to `private, no-store`
- Purge existing CDN cache
- Status after Phase 1: `MITIGATED — NOT CLOSED`

**Phase 2 (Complete):** Use SIGNED URL (Option 3) to fully close SEC-001:
- Implement signing endpoint and verification middleware
- Migrate Canvas to Stable Asset References
- Enforce project scope in R2 key structure and token validation
- Status after Phase 2: `CLOSED`

---

## Secret Separation from LP_CODE

LP_CODE is an application-level passcode for user authentication. It:
- Is visible to the user
- May be shared (sent in a link, written down)
- Is not designed as a cryptographic secret
- Has no key rotation mechanism appropriate for an HMAC signing key

ASSET_SIGNING_SECRET is a server-side cryptographic signing key. It:
- MUST have at least 256 bits of entropy
- MUST be stored in Cloudflare's encrypted secret store
- MUST never be transmitted to the browser or any client
- MUST have a key rotation procedure (kid versioning, grace period)
- MUST be distinct from LP_CODE with no derivation relationship

These are fundamentally different credentials for different purposes. Conflation would:
- Allow any LP_CODE holder to forge signed URLs for any asset
- Eliminate the security value of the signed URL scheme
- Require all users to rotate their "access code" every time signing keys rotate

The separation is absolute and non-negotiable.

---

## Stable Asset Reference

Canvas state (persisted in KV, D1, R2 object metadata, or localStorage) MUST store
a **Stable Asset Reference** that:
- Survives URL scheme changes (Phase 1 → Phase 2 migration)
- Can be used to re-request a signed URL at any time
- Is not a signed URL itself (signed URLs expire; stable references do not)

Required format:
```json
{
  "type": "asset_ref",
  "asset_key": "projects/{pid}/assets/{type}/{uuid}.{ext}",
  "project_id": "{pid}",
  "content_type": "image/jpeg",
  "width": 1024,
  "height": 1024
}
```

Loading sequence:
1. Canvas loads; reads Stable Asset References from persisted state
2. Canvas calls `POST /api/assets/urls` with all asset keys (batch)
3. Backend returns signed URLs valid for 3600 seconds
4. Canvas renders images using signed URLs
5. On HTTP 401 from asset endpoint (token expired): re-call step 2 for that asset

---

## Cache Strategy

| Mode | Cache-Control | Rationale |
|---|---|---|
| LP_CODE mode (private) | `private, no-store` | Assets are private; must not be cached by CDN or shared caches |
| Public portfolio mode (future, opt-in) | `public, max-age=3600` | Requires explicit Founder decision and product code activation |
| Upload presigned URL | N/A (PUT, no caching) | Upload is one-time, write-only |
| Export download | `private, no-store` | Same as LP_CODE mode; download is user-specific |

`Cache-Control: private, no-store` prevents:
- Cloudflare CDN from caching the response
- Browser from writing a cache file
- Proxy caches from storing the response

---

## Migration Plan

### Phase 1 (No migration required)
- Direct auth guard added to existing asset endpoint
- Cache-Control changed
- No R2 key changes
- No canvas state changes

### Phase 2 (Migration required if R2 keys lack project scope)

IF (after inspection) R2 object keys do NOT include project scope prefix:

1. **Copy, don't move:** copy all existing R2 objects to new scoped keys
   `projects/{pid}/assets/{type}/{uuid}.{ext}`. Keep originals during transition.
2. **Update metadata:** add `project_id` and `asset_key` to R2 object metadata.
3. **Update canvas:** migrate persisted canvas state from old URLs/keys to new
   Stable Asset Reference format. May require a one-time migration script.
4. **Grace period:** both old and new key paths accepted for N days.
5. **Deprecate old keys:** after grace period, remove old R2 objects.

IF R2 object keys already include project scope (UNKNOWN — requires inspection):
- Migration may be limited to metadata update and canvas state format change only.

### Key Insight: Copy Before Delete
Old R2 object keys should never be deleted before canvas state is migrated. A canvas
referencing an old key after the object is deleted will show broken images.

---

## Security Consequences

| Consequence | Assessment |
|---|---|
| Phase 1 closes unauthorized access | YES — LP_CODE guard prevents unauthenticated reads |
| Phase 1 closes CDN cache exposure | YES — `private, no-store` prevents CDN caching |
| Phase 1 closes URL leakage | PARTIALLY — no expiry on auth guard; URL + LP_CODE always works |
| Phase 2 closes URL leakage | YES — signed URL expires; leaked URL is useless after TTL |
| Phase 2 closes project boundary | YES — `pid` in token enforces project scope |
| Phase 2 closes replay attacks | YES — token bound to specific asset key |
| Timing attack surface | LOW — constant-time comparison required (§4.5 req 7) |
| Secret rotation overhead | MEDIUM — requires wrangler secret commands per rotation |
| Canvas latency (batch signing) | LOW — batch endpoint minimizes round trips |

---

## Operational Consequences

| Item | Impact |
|---|---|
| ASSET_SIGNING_SECRET management | Developer must rotate via `wrangler secret put` per rotation schedule |
| Canvas token refresh | Frontend must handle 401 → re-sign gracefully (no user impact) |
| Export changed | Export must call signing endpoint for all included assets |
| Share link changed | Share links must use a different token or auth mechanism (FD-004) |
| R2 key migration | One-time operational task; reversible if old objects retained |
| Purge on deploy | `wrangler purge_cache` (or Cloudflare dashboard purge) required after Phase 1 |

---

## Rollback

**Phase 1 rollback:**
- Revert `functions/_lib.js` to pre-Phase-1 version
- Redeploy to Cloudflare Pages
- SEC-001 re-opens to OPEN status; document in ISSUE_REGISTRY.json

**Phase 2 rollback:**
- Revert signing endpoint and verification middleware
- Retain both old and new R2 key paths during rollback window
- Canvas state migration may require a second migration (back to old format)
- Complex; plan carefully before Phase 2 deployment

---

## Founder Decisions Required

| ID | Question | Default |
|---|---|---|
| FD-001 | Multi-tenant backward compatibility: clean-break or backward-compatible migration? | backward_compatible |
| FD-002 | Project Delete: delete R2 objects, soft-delete, or access-revoke? | No default — Founder must decide |
| FD-003 | Export: same permission as access, or separate tier? | No default — Founder must decide |
| FD-004 | Share feature: recipient auth required, or temporary-public URL? | No default — Founder must decide |
| FD-005 | Public portfolio mode: will this feature ever exist? | Assume no — if yes, requires separate ADR |

Decisions FD-001 through FD-005 are required before Phase 2 design can be finalized.
Phase 1 does not require any Founder decisions beyond "authorize implementation."
