"""V3.0 Sprint 4 — Calibration Engine.

Verifies whether the engine's confidence scores have *empirical* meaning:
- Brier score (lower = better; perfect calibration = 0)
- Log loss (penalises overconfidence)
- Expected Calibration Error (ECE): bucket predictions into bands and measure
  how far average predicted probability deviates from observed hit rate.
- Per-confidence-band hit rate (Confidence Bucket Performance).

The engine reads from ``results`` + ``recommendations`` + ``classifications``
tables and produces a ``CalibrationReport``. Pure aggregator — no I/O beyond
the SQLite read.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class BucketStat:
    """One confidence bucket's calibration."""

    bucket_lo: int  # inclusive percentage, e.g. 90 for A+
    bucket_hi: int  # exclusive
    n: int
    avg_predicted: float  # 0..1 — the avg classifier confidence in bucket
    observed_hit_rate: float  # 0..1 — fraction with corridor_hit=True
    main_win_rate: float  # 0..1 — fraction main_result in {win, half_win}


@dataclass(frozen=True)
class CalibrationReport:
    """Output of ``calibrate``."""

    n_reviewed: int
    brier: float
    log_loss: float
    expected_calibration_error: float
    buckets: list[BucketStat] = field(default_factory=list)


# Default bucket bands (inclusive low, exclusive high) — match the A+/A/B/C/D
# bands in stake.py × precision_score thresholds in precision_score_rules.yaml.
DEFAULT_BUCKETS: list[tuple[int, int]] = [
    (0, 60),
    (60, 70),
    (70, 80),
    (80, 90),
    (90, 101),  # 101 to include 100
]


def calibrate(
    conn: sqlite3.Connection,
    *,
    buckets: list[tuple[int, int]] | None = None,
    since: str | None = None,
) -> CalibrationReport:
    """Compute Brier / log-loss / ECE / per-bucket performance from saved reviews."""
    band_list = buckets or DEFAULT_BUCKETS

    where = ["1=1"]
    params: list = []
    if since:
        where.append("res.reviewed_at >= ?")
        params.append(since)

    rows = conn.execute(
        "SELECT res.review_json, c.confidence "
        "FROM results res "
        "JOIN classifications c ON c.match_id = res.match_id "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY res.match_id",
        params,
    ).fetchall()

    if not rows:
        return CalibrationReport(
            n_reviewed=0, brier=0.0, log_loss=0.0,
            expected_calibration_error=0.0, buckets=[],
        )

    n = len(rows)
    predictions: list[tuple[float, bool, str]] = []  # (predicted_prob, corridor_hit, main_result)
    for row in rows:
        rev = json.loads(row["review_json"])
        confidence = float(Decimal(row["confidence"]))
        predictions.append((confidence, bool(rev["corridor_hit"]), rev["main_result"]))

    # Brier = mean((p - outcome)^2). Outcome = corridor_hit as 0/1.
    brier = sum((p - (1.0 if hit else 0.0)) ** 2 for p, hit, _ in predictions) / n

    # Log loss with clamp.
    eps = 1e-9
    log_loss = -sum(
        ((1.0 if hit else 0.0) * math.log(max(p, eps))
         + (1.0 - (1.0 if hit else 0.0)) * math.log(max(1 - p, eps)))
        for p, hit, _ in predictions
    ) / n

    # Bucket stats.
    bucket_stats: list[BucketStat] = []
    ece_terms: list[tuple[int, float]] = []
    for lo, hi in band_list:
        in_bucket = [
            (p, hit, mr) for p, hit, mr in predictions
            if int(p * 100) >= lo and int(p * 100) < hi
        ]
        if not in_bucket:
            continue
        nb = len(in_bucket)
        avg_p = sum(p for p, _, _ in in_bucket) / nb
        hit_rate = sum(1 for _, hit, _ in in_bucket if hit) / nb
        win_rate = sum(1 for _, _, mr in in_bucket if mr in ("win", "half_win")) / nb
        bucket_stats.append(BucketStat(
            bucket_lo=lo, bucket_hi=hi, n=nb,
            avg_predicted=avg_p, observed_hit_rate=hit_rate, main_win_rate=win_rate,
        ))
        ece_terms.append((nb, abs(avg_p - hit_rate)))

    ece = sum(nb * dev for nb, dev in ece_terms) / n if ece_terms else 0.0

    return CalibrationReport(
        n_reviewed=n,
        brier=brier,
        log_loss=log_loss,
        expected_calibration_error=ece,
        buckets=bucket_stats,
    )


__all__ = ["DEFAULT_BUCKETS", "BucketStat", "CalibrationReport", "calibrate"]
