"""V3.0 Sprint 2 — Consensus Engine.

Given the latest snapshot per bookmaker for one (match, market) pair, computes
market consensus and tags the user's platform as ``better_than_market`` /
``worse_than_market`` / ``in_line``.

Two ideas matter:

1. **Edge classification** — for AH/totals, the median line across books is the
   consensus. A user-platform line that gives the bettor a softer favorite
   (less negative handicap, higher Under line) is "better_than_market".
2. **Agreement detection** — when most books cluster within ``stable_band`` of
   the median, agreement is "high"; outliers are flagged separately.

Pure function — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from tigerline.models import OddsSnapshot

CONFIG_DIR = Path(__file__).parent / "config"

Agreement = Literal["high", "medium", "low"]
PlatformEdge = Literal["better_than_market", "in_line", "worse_than_market", "no_user_book"]


def _to_decimal(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    return v


YamlDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


class ConsensusThresholds(BaseModel):
    """Tunable consensus thresholds (line units, e.g. 0.25 = one quarter goal)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    in_line_band: YamlDecimal = Field(default=Decimal("0.05"))
    high_agreement_band: YamlDecimal = Field(default=Decimal("0.05"))
    medium_agreement_band: YamlDecimal = Field(default=Decimal("0.15"))
    outlier_band: YamlDecimal = Field(default=Decimal("0.25"))


@lru_cache(maxsize=1)
def load_consensus_rules() -> ConsensusThresholds:
    path = CONFIG_DIR / "movement_rules.yaml"  # share file with movement
    if not path.exists():
        return ConsensusThresholds()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    consensus = raw.get("consensus", {}) if isinstance(raw, dict) else {}
    return ConsensusThresholds.model_validate(consensus)


def reset_consensus_cache() -> None:
    load_consensus_rules.cache_clear()


@dataclass(frozen=True)
class ConsensusReport:
    """Output of ``compute_consensus``."""

    match_id: str
    market_type: str
    n_books: int
    consensus_line: Decimal | None
    user_platform_line: Decimal | None
    user_platform_edge: PlatformEdge
    edge_magnitude: Decimal
    agreement: Agreement
    outlier_books: list[str]


def compute_consensus(
    snapshots_per_book_latest: list[OddsSnapshot],
    *,
    user_bookmaker: str | None = None,
    thresholds: ConsensusThresholds | None = None,
) -> ConsensusReport | None:
    """Given one latest snapshot per book, compute the consensus + user edge."""
    if not snapshots_per_book_latest:
        return None
    t = thresholds or load_consensus_rules()

    lines = [s.line for s in snapshots_per_book_latest if s.line is not None]
    if not lines:
        return ConsensusReport(
            match_id=snapshots_per_book_latest[0].match_id,
            market_type=snapshots_per_book_latest[0].market_type,
            n_books=len(snapshots_per_book_latest),
            consensus_line=None,
            user_platform_line=None,
            user_platform_edge="no_user_book",
            edge_magnitude=Decimal("0"),
            agreement="low",
            outlier_books=[],
        )

    consensus = Decimal(str(median(lines)))

    # Agreement: how tightly do books cluster around the median?
    max_dev = max(abs(line - consensus) for line in lines)
    if max_dev <= t.high_agreement_band:
        agreement: Agreement = "high"
    elif max_dev <= t.medium_agreement_band:
        agreement = "medium"
    else:
        agreement = "low"

    outliers = [
        s.bookmaker
        for s in snapshots_per_book_latest
        if s.line is not None and abs(s.line - consensus) >= t.outlier_band
    ]

    user_snap = (
        next((s for s in snapshots_per_book_latest if s.bookmaker == user_bookmaker), None)
        if user_bookmaker
        else None
    )
    if user_snap is None or user_snap.line is None:
        edge: PlatformEdge = "no_user_book"
        edge_mag = Decimal("0")
        user_line = None
    else:
        user_line = user_snap.line
        diff = user_line - consensus
        if abs(diff) <= t.in_line_band:
            edge = "in_line"
        elif diff > 0:
            # User line is *less* negative than market → softer favorite line → better for backer
            edge = "better_than_market"
        else:
            edge = "worse_than_market"
        edge_mag = abs(diff)

    return ConsensusReport(
        match_id=snapshots_per_book_latest[0].match_id,
        market_type=snapshots_per_book_latest[0].market_type,
        n_books=len(snapshots_per_book_latest),
        consensus_line=consensus,
        user_platform_line=user_line,
        user_platform_edge=edge,
        edge_magnitude=edge_mag,
        agreement=agreement,
        outlier_books=sorted(outliers),
    )


__all__ = [
    "Agreement",
    "ConsensusReport",
    "ConsensusThresholds",
    "PlatformEdge",
    "compute_consensus",
    "load_consensus_rules",
    "reset_consensus_cache",
]
