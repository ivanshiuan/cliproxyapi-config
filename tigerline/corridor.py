"""Score corridor builder.

Templates are stored in ``corridor_templates.yaml`` with the favorite as home.
At runtime we flip the tuples if the away side is the actual favorite, so the
caller always reads (home_goals, away_goals) — same orientation as ReviewResult.
"""

from __future__ import annotations

from tigerline.models import MatchInput, ScenarioType, ScoreCorridor
from tigerline.rules import CorridorTemplate, load_corridor_templates


def build_corridor(
    scenario: ScenarioType,
    match: MatchInput,
    templates: dict[ScenarioType, CorridorTemplate] | None = None,
) -> ScoreCorridor:
    """Return the score corridor for ``scenario``, oriented to the match.

    Templates are written favorite-as-home. We mirror to (away, home) when the
    match's favorite is the away side.
    """
    if templates is None:
        templates = load_corridor_templates()

    template = templates.get(scenario)
    if template is None:
        # Defensive: a new scenario added to ScenarioType without a template.
        # Don't crash a live analysis — degrade to "unclear_skip".
        template = templates["unclear_skip"]

    flip = match.market.favorite == "away"
    scores = [_orient(s, flip) for s in template.scores]
    primary = _orient(template.primary, flip)

    # De-duplicate while preserving order (mirroring can produce duplicates if
    # symmetric, e.g. (1,1)).
    seen: set[tuple[int, int]] = set()
    deduped: list[tuple[int, int]] = []
    for s in scores:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)

    if primary not in deduped:
        deduped.insert(0, primary)

    return ScoreCorridor(scores=deduped, primary=primary)


def _orient(score: tuple[int, int], flip: bool) -> tuple[int, int]:
    return (score[1], score[0]) if flip else score


__all__ = ["build_corridor"]
