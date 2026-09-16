# STAGING-RISK-REGISTER

---

## Risk Classification

- **Severity:** CRITICAL / HIGH / MEDIUM / LOW
- **Likelihood:** HIGH / MEDIUM / LOW
- **Status:** OPEN / MITIGATED / ACCEPTED

---

## Risk Matrix

| ID | Risk | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R01 | AP account maps to Odoo default `211000` instead of `2100` | HIGH | HIGH | Step 7 of runbook explicitly configures 2100 and verifies via acceptance check C5 | OPEN |
| R02 | Odoo image upgraded by provider, breaks pinned digest | HIGH | LOW | Image pulled with digest pin; no auto-updates; manual upgrade process required | MITIGATED (pin) |
| R03 | API key leaked in application logs | HIGH | LOW | `api_key_in_repr: False` verified in sandbox; log redaction in `restaurant_api/middleware`; acceptance check B5 | MITIGATED |
| R04 | Service account granted admin rights by mistake | HIGH | LOW | Provisioning script explicitly lists minimum groups; acceptance check B1 verifies | MITIGATED |
| R05 | Odoo tax rules auto-apply to accounts 1310/1360 | HIGH | MEDIUM | `EXPLICIT_INPUT_VAT_LINE_COMPATIBILITY_MODE` documented; acceptance check D5 verifies only expected accounts | OPEN |
| R06 | Docker daemon dies between runs (ephemeral VM) | MEDIUM | MEDIUM | `restart: unless-stopped` in compose; systemd service for dockerd; monitoring | OPEN |
| R07 | PO sync creates duplicate vendor bills on partial failure | MEDIUM | LOW | Search-based recovery implemented in `61ad837`; stamp-loss replay tested in sandbox | MITIGATED |
| R08 | Non-TWD orders silently accepted | MEDIUM | LOW | `UnsupportedCurrencyError` raised pre-egress; negative case F1 in acceptance | MITIGATED |
| R09 | Journal code `PUR` not present in staging Odoo | MEDIUM | MEDIUM | Step 7 of runbook creates journal; acceptance check C4 | OPEN |
| R10 | Secrets committed to git accidentally | HIGH | LOW | `.env` in `.gitignore`; `make full-check` includes secret check; pre-commit hook | MITIGATED |
| R11 | Odoo port 18069 accidentally exposed to internet | HIGH | LOW | Compose binds `127.0.0.1:18069` only; acceptance check A4 verifies from external | MITIGATED |
| R12 | Database named `production` / `prod` / `live` | MEDIUM | LOW | Runbook specifies `resto_staging`; Docker compose env specifies `POSTGRES_DB=resto_staging` | MITIGATED |
| R13 | Restore drill never executed before production authorization | HIGH | HIGH | Acceptance checklist Section I makes restore drill mandatory | OPEN |
| R14 | `property_account_payable_id` not set on partners, wrong AP account | HIGH | MEDIUM | Accounting config doc Step 3 addresses this; acceptance check D6 verifies account 2100 in payable line | OPEN |
| R15 | `restaurant_api` production DB connected accidentally | CRITICAL | LOW | Staging `.env` uses `resto_staging_app` DB; production DB not reachable from staging network | MITIGATED |
| R16 | Production Odoo instance called from staging | CRITICAL | LOW | Staging `.env` `ODOO_URL=http://127.0.0.1:18069`; no production URL in staging config | MITIGATED |
| R17 | Dead-letter POs accumulate silently (5 attempts exceeded) | MEDIUM | MEDIUM | `reconcile_sync_status` endpoint reports dead-letter count; alerting TBD | OPEN |
| R18 | Phase 2 tax integration misidentified as Phase 1 | MEDIUM | LOW | `EXPLICIT_INPUT_VAT_LINE_COMPATIBILITY_MODE` constant in code; Tax Strategy doc separation clear | MITIGATED |

---

## Open Risk Summary (must close before production authorization)

- **R01** — AP account: close via acceptance check C5 + D6
- **R05** — Tax auto-apply: close via manual Odoo config review +
  acceptance check D5
- **R06** — Daemon restart: close via systemd docker service configuration
- **R09** — PUR journal: close via runbook step 7 + acceptance check C4
- **R13** — Restore drill: close by executing restore drill and recording
  result
- **R14** — Partner AP account: close via provisioning script + acceptance
  check D6
- **R17** — Dead-letter alerting: close via monitoring setup (can be
  deferred to production)
