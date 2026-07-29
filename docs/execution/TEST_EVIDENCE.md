# TEST EVIDENCE — SEC-001

**Last Updated:** 2026-07-29  
**Work Package:** SEC-001-SPEC-CORRECTION

---

## Corrected Evidence Table

| Evidence Item | Previous Claim | Corrected State | Basis for Correction |
|---|---|---|---|
| Local Unauthorized Access | CONFIRMED | **CONFIRMED** | Retained — local test is valid evidence of local behavior |
| Cloud Function Missing Auth Guard | CONFIRMED | **CONFIRMED** | Retained — code inspection showed auth guard absent on asset path |
| Production Unauthorized Access | CONFIRMED | **NOT TESTED** | Corrected — local test with Wrangler/Miniflare ≠ production Cloudflare. `CF-Cache-Status` and actual CDN routing were NOT verified against a live Pages URL |
| Cache-Control Public Exposure | CONFIRMED | **CONFIRMED** | Retained — response header was observed with `public` value |
| Actual Cloudflare CDN Cache HIT | CONFIRMED | **NOT TESTED** | Corrected — a `Cache-Control: public` response header does not prove Cloudflare stored or served a cached copy. `CF-Cache-Status: HIT` and `Age` header on a subsequent anonymous request are required; these were not measured |
| Cache Isolation | FAIL | **FAIL** | Retained — if CDN does cache (not yet confirmed), isolation fails because no auth is checked on cached responses |
| Severity | P1 | **P1** | Retained |

---

## Evidence Items Needed for Production Verification

The following evidence items are **planned** but must NOT be collected until Phase 1 or Phase 2
is deployed to a non-production environment and authorized for testing.

| Evidence Item | Test Method | Status |
|---|---|---|
| Production Unauthorized Access | `curl -I https://{pages}.dev/api/assets/{key}` with no auth | NOT TESTED |
| Cloudflare CDN Cache HIT | Two sequential anonymous requests; check `CF-Cache-Status` on second | NOT TESTED |
| Age header value | `curl -I` and inspect `Age:` header | NOT TESTED |
| Browser cache isolation | Open asset URL in new private window after first authenticated load | NOT TESTED |
| Cache purge effectiveness | Purge URL via Cloudflare API; verify next request gives `CF-Cache-Status: MISS` | NOT TESTED |

---

## Evidence Items Confirmed in This Package

None — this work package is documentation only. No new tests were run.

---

## Test Run History

| Date | Type | Scope | Result | Notes |
|---|---|---|---|---|
| 2026-07-29 | Documentation | SEC-001-SPEC-CORRECTION | COMPLETED | No product code changed; no tests run |

---

## Prior Evidence (From Previous Work Package — Not Re-Verified)

The following were claimed as confirmed in a prior work package. They are accepted as
confirmed for local behavior but reclassified as stated above for production claims.

- **Local Unauthorized Asset Read:** a request to `/api/assets/{key}` without LP_CODE
  succeeded locally in a Wrangler/Miniflare dev environment.
- **Cache-Control: public header:** the asset response included `Cache-Control: public` (or
  similar `public` directive) in the dev environment response.
- **Code path missing auth guard:** review of `functions/_lib.js` showed the asset fetch
  path does not call the LP_CODE verification function before forwarding to R2.
