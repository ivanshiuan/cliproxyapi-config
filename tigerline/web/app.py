"""FastAPI adapter for the TIGER LINE PRIME decision pipeline.

The web layer is a thin JSON transport: it validates incoming JSON with the
same Pydantic models the CLI uses, calls the recommender/review functions
directly, and returns their outputs. No new business logic lives here.

Design notes
------------
- **Stateless** by default. Sprint 7 does not require a database — every
  request contains its own MatchInput (and, for review, the previously
  returned BetPlan). This keeps the deploy target free of stateful storage
  and makes the frontend safe to serve from a CDN edge.
- **CORS is permissive**. There is nothing to protect (no auth, no writes to
  shared state); allowing any origin lets the same backend serve a static
  frontend hosted on a different domain.
- **Compliance disclaimer** is appended to any BetPlan-shaped response as a
  top-level ``disclaimer`` field — the frontend renders it verbatim.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tigerline.classifier import classify
from tigerline.compliance_guard import DEFAULT_DISCLAIMER_EN, DEFAULT_DISCLAIMER_ZH
from tigerline.corridor import build_corridor
from tigerline.harness import evaluate as evaluate_harness
from tigerline.models import BetPlan, MatchInput, ReviewResult
from tigerline.recommender import recommend
from tigerline.review import review

_DISCLAIMER = f"{DEFAULT_DISCLAIMER_ZH}\n\n{DEFAULT_DISCLAIMER_EN}"

_HERE = Path(__file__).resolve().parent
_STATIC_DIR = _HERE / "static"
_EXAMPLES_DIR = _HERE.parent / "examples"

app = FastAPI(
    title="TIGER LINE PRIME",
    description=(
        "Asian-handicap-first World Cup match decision system. "
        "Not a predictor — a bet-line landing classifier. "
        "This service is decision support, not a profit guarantee."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ──────────────────────────────────────────────────────────────────────────
# Response envelopes
# ──────────────────────────────────────────────────────────────────────────


class AnalyzeResponse(BaseModel):
    """Full decision packet — everything the frontend needs to render."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    confidence: str
    reasons: list[str] = Field(default_factory=list)
    harness: str
    harness_notes: list[str] = Field(default_factory=list)
    corridor_scores: list[list[int]] = Field(default_factory=list)
    corridor_primary: list[int] = Field(default_factory=list)
    plan: BetPlan
    disclaimer: str = _DISCLAIMER


class ReviewRequest(BaseModel):
    """Post-match review input — match + prior plan + actual score."""

    model_config = ConfigDict(frozen=True)

    match: MatchInput
    plan: BetPlan
    home_goals: int = Field(ge=0, le=20)
    away_goals: int = Field(ge=0, le=20)


class ReviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: ReviewResult
    disclaimer: str = _DISCLAIMER


# ──────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — no dependencies, always green if the process is up."""
    return {"status": "ok", "service": "tigerline", "version": app.version}


@app.get("/", include_in_schema=False)
def index() -> Response:
    """Serve the single-page frontend."""
    html = _STATIC_DIR / "index.html"
    if not html.exists():
        return JSONResponse(
            {
                "error": "frontend_missing",
                "message": "static/index.html not bundled — API endpoints still work",
                "endpoints": ["/api/analyze", "/api/review", "/api/examples"],
            }
        )
    return FileResponse(str(html), media_type="text/html")


@app.post("/api/analyze", response_model=AnalyzeResponse)
def api_analyze(match: MatchInput) -> AnalyzeResponse:
    """Run the full pipeline: classify → corridor → harness → recommend."""
    classification = classify(match)
    corridor = build_corridor(classification.scenario, match)
    verdict = evaluate_harness(match, classification)
    plan = recommend(match)
    return AnalyzeResponse(
        scenario=classification.scenario,
        confidence=str(classification.confidence),
        reasons=classification.reasons,
        harness=verdict.adjustment,
        harness_notes=verdict.notes,
        corridor_scores=[[h, a] for h, a in corridor.scores],
        corridor_primary=list(corridor.primary),
        plan=plan,
    )


@app.post("/api/review", response_model=ReviewResponse)
def api_review(payload: ReviewRequest) -> ReviewResponse:
    """Post-match review — 0-100 scorecard + rule-tuning suggestions."""
    result = review(payload.match, payload.plan, payload.home_goals, payload.away_goals)
    return ReviewResponse(result=result)


@app.get("/api/examples")
def api_examples_list() -> dict[str, list[str]]:
    """List the bundled example match files (frontend uses these to prefill)."""
    if not _EXAMPLES_DIR.exists():
        return {"examples": []}
    names = sorted(p.stem for p in _EXAMPLES_DIR.glob("*.json") if p.stem != "snapshot_belgium_t1h")
    return {"examples": names}


@app.get("/api/examples/{name}")
def api_examples_get(name: str) -> MatchInput:
    """Return one prefilled MatchInput for the frontend to load into the form."""
    # Guard against path traversal — only accept a bare stem.
    if "/" in name or ".." in name or name.startswith("."):
        raise HTTPException(status_code=400, detail="invalid example name")
    path = _EXAMPLES_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"example '{name}' not found")
    try:
        return MatchInput.model_validate_json(path.read_text())
    except ValidationError as exc:  # pragma: no cover — bundled examples are validated in tests
        raise HTTPException(status_code=500, detail=f"example malformed: {exc}") from exc


__all__ = ["app"]
