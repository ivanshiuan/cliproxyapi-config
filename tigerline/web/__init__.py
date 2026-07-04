"""TIGER LINE PRIME — Web deployment layer.

Sprint 7: FastAPI wrapper + single-page frontend so the CLI-only decision
system becomes usable from a phone browser without a terminal.

The web layer is intentionally thin — it JSON-adapts the existing
``tigerline`` package (models, classifier, recommender, review) rather than
duplicating logic. The core decision pipeline is the same code the CLI uses.

Endpoints:
    GET  /                    → static frontend (single-page)
    GET  /health              → liveness probe
    POST /api/analyze         → MatchInput JSON → BetPlan JSON
    POST /api/review          → match + plan + score → ReviewResult
    GET  /api/examples        → list of bundled example match filenames
    GET  /api/examples/{name} → prefilled MatchInput JSON

Persistence is deliberately out of scope for Sprint 7 — the browser session
holds state. Adding SQLite + a Fly volume is a follow-up.
"""

from tigerline.web.app import app

__all__ = ["app"]
