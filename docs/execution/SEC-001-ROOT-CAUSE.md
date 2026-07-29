# SEC-001 — Root Cause Analysis

**Issue ID:** SEC-001  
**Title:** Unauthorized Asset Read  
**Severity:** P1  
**Status:** OPEN  
**Last Updated:** 2026-07-29

---

## 1. Corrected Evidence State

| Evidence Item | Previous Claim | Corrected State | Notes |
|---|---|---|---|
| Local Unauthorized Access | CONFIRMED | **CONFIRMED** | Asset served without LP_CODE in local test |
| Cloud Function Missing Auth Guard | CONFIRMED | **CONFIRMED** | `functions/_lib.js` asset path skips LP_CODE check |
| Production Unauthorized Access | CONFIRMED ❌ | **NOT TESTED** | Local test ≠ production; Cloudflare routing may differ |
| Cache-Control Public Exposure | CONFIRMED | **CONFIRMED** | Response header set to `public` in LP_CODE mode |
| Actual Cloudflare CDN Cache HIT | CONFIRMED ❌ | **NOT TESTED** | A `Cache-Control: public` header does not prove Cloudflare stored or served a cached response; `CF-Cache-Status` and `Age` must be checked against a real Cloudflare origin request |
| Cache Isolation | FAIL | **FAIL** | If CDN did cache, cached response is served to unauthenticated requestors |
| Severity | P1 | **P1** | Maintained |

> **Principle:** A local test with a dev Miniflare/Wrangler environment is not equivalent to a
> production Cloudflare CDN test. Production verification requires an anonymous `curl` against
> the live Pages URL, inspection of `CF-Cache-Status`, and inspection of `Age` header. These
> tests must be planned but are explicitly deferred until Phase 1 implementation is authorized.

---

## 2. Root Cause

### Primary Cause

The asset-serving endpoint (within `functions/_lib.js` or a related route handler) applies
LP_CODE authentication to most routes but **does not apply the same guard to the R2 asset
fetch path**. A caller who knows the object key (or can enumerate/guess it) can retrieve any
stored asset without providing LP_CODE.

### Contributing Cause A — No Scoped Object Keys

R2 object keys (UNKNOWN — not inspected) are likely flat UUIDs or generation-timestamp paths
with no project scope prefix. This means:

- All assets share the same key space
- Knowing any one key grants access to any other key in the same namespace
- There is no key structure that enforces project or user boundary

### Contributing Cause B — Cache-Control: public

The asset response sets `Cache-Control: public` (or similar) even when LP_CODE mode is
active. If Cloudflare caches this response:

- A subsequent anonymous request gets the cached asset with no origin contact
- LP_CODE is never checked for cache-hit responses
- Cache lifetime extends unauthorized access indefinitely

### Contributing Cause C — Signed URL Absent

The frontend loads assets using direct R2 object paths (or a thin proxy with no token).
There is no time-limited, scope-bound signed URL protecting individual assets. Any URL
leaked (e.g., in browser history, shared screenshots, logs) remains valid indefinitely.

### Contributing Cause D — No Project-Scoped Asset Reference

Canvas state (UNKNOWN — not inspected) likely stores full URLs or bare object keys without
project scope. This prevents future enforcement at the object-key level without a migration.

---

## 3. Attack Surface

| Vector | Description | Exploitability |
|---|---|---|
| Direct URL access | Attacker knows or guesses the R2 object key and fetches without LP_CODE | HIGH if keys are predictable; MEDIUM if UUID-v4 |
| Leaked URL from sharing | User shares a canvas screenshot or link containing the asset URL | HIGH — no expiry |
| Browser cache | Asset cached in browser can be accessed after LP_CODE session ends | HIGH |
| CDN cache | Asset cached in Cloudflare CDN served to anonymous requestors | HIGH if CDN is caching (NOT TESTED) |
| Log leakage | Cloudflare Access logs / Wrangler logs capture asset URLs | MEDIUM |
| Canvas export | Exported canvas contains embedded asset URLs | MEDIUM |

---

## 4. Impact Assessment

| Impact Category | Description |
|---|---|
| Confidentiality | Any uploaded or AI-generated image can be read without authentication |
| Data ownership | User assets are not protected by access boundary |
| Regulatory | If project stores personal images, may violate user privacy expectations |
| Reputational | Users expect uploaded content to be private in LP_CODE mode |
| Severity | **P1** — confirmed unauthorized read path in production-equivalent code |
