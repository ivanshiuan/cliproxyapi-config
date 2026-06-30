"""DevSwarm prompt-regression evals.

Closes the loop that `prompts/_versions.py` and `codex.py` open: pinning a
version is only useful if something checks that a given version still behaves.
These evals feed known-good and known-bad artifacts to the Reviewer node and
assert it reaches the right verdict and cites the right Codex invariant — so a
prompt or Codex change that regresses adjudication quality is caught.

Two modes:
- Structural / mocked (no API key): validates the golden set is well-formed and
  the harness wiring (write files → reviewer_node → score) works end to end with
  a scripted client.
- Live (ANTHROPIC_API_KEY set): runs the real Reviewer model over the golden set
  and scores verdict + basis match. Run via `python -m devswarm.evals`.
"""

from .cases import GOLDEN_CASES, EvalCase
from .runner import EvalResult, run_all, run_case, score

__all__ = [
    "GOLDEN_CASES",
    "EvalCase",
    "EvalResult",
    "run_all",
    "run_case",
    "score",
]
