"""V3.0 data-quality guard.

Every input that enters the V3.0 layer goes through ``check_metadata`` and
``check_snapshot``. If metadata is missing, downstream engines refuse to
score — they output ``unclear_skip`` or downgrade rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tigerline.models import OddsSnapshot, SourceConfidence, SourceMetadata


@dataclass(frozen=True)
class DataIssue:
    """One thing wrong with a piece of input."""

    code: str
    message: str
    severity: str  # "block" | "warn"


def check_metadata(meta: SourceMetadata, *, now: datetime | None = None) -> list[DataIssue]:
    """Audit a ``SourceMetadata``. Returns the issues found (empty = clean)."""
    issues: list[DataIssue] = []
    current = now or datetime.now(UTC)

    if meta.collected_at.tzinfo is None:
        issues.append(
            DataIssue(
                code="naive_timestamp",
                message="collected_at must be timezone-aware (use UTC)",
                severity="block",
            )
        )
    elif meta.collected_at > current + timedelta(days=7):
        issues.append(
            DataIssue(
                code="future_timestamp",
                message=f"collected_at {meta.collected_at.isoformat()} is in the future",
                severity="block",
            )
        )

    if meta.source == "manual" and meta.source_confidence == "low":
        issues.append(
            DataIssue(
                code="low_confidence_manual",
                message="manual entry with low confidence — verify before scoring",
                severity="warn",
            )
        )

    return issues


def check_snapshot(snap: OddsSnapshot, *, now: datetime | None = None) -> list[DataIssue]:
    """Audit a snapshot — both the metadata envelope and its body."""
    issues = list(check_metadata(snap.metadata, now=now))

    requires_line = snap.market_type in {
        "AH_full",
        "AH_first_half",
        "totals_full",
        "totals_first_half",
    }
    if requires_line and snap.line is None:
        issues.append(
            DataIssue(
                code="missing_line",
                message=f"market_type={snap.market_type} requires a numeric line",
                severity="block",
            )
        )
    if not requires_line and snap.line is not None:
        issues.append(
            DataIssue(
                code="spurious_line",
                message=f"market_type={snap.market_type} should not carry a line",
                severity="warn",
            )
        )
    return issues


def is_blocked(issues: list[DataIssue]) -> bool:
    """True if any issue has ``severity=block``."""
    return any(i.severity == "block" for i in issues)


def confidence_score(meta: SourceMetadata) -> int:
    """Map source confidence to a 0-100 score for the precision-score engine."""
    bands: dict[SourceConfidence, int] = {"high": 90, "medium": 70, "low": 40}
    base = bands[meta.source_confidence]
    if meta.is_verified:
        base = min(100, base + 5)
    return base


__all__ = [
    "DataIssue",
    "check_metadata",
    "check_snapshot",
    "confidence_score",
    "is_blocked",
]
