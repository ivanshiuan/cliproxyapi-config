# Security Review (security-review skill, two-stage agent flow)

Stage 1 — identification sub-agent: read the full branch diff (committed +
working tree), compared against existing repo security patterns
(api/errors.py, api/deps.py, middleware, integrations/line), examined the
JSON-RPC client policy chokepoint, sync service data flow, migrations and
config for: injection, authz bypass, secret exposure, deserialization, data
leakage. Confidence bar: >80% exploitability.

Result: **NO_FINDINGS** — no high-confidence vulnerability newly added by
this branch. Stage 2 (false-positive filtering) not applicable with zero findings.

Supporting adversarial evidence (test-enforced, not just reviewed):
policy-block tests raise BEFORE network egress; api_key leak assertions over
repr/str/exception/caplog; balanced-entry invariant at the wire boundary.
