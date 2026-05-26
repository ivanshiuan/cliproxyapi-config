"""Shared utilities for agent nodes."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from ..llm import Usage
from ..state import TelemetryMsg


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Timer:
    """Tiny context manager that records wall-clock duration."""

    def __init__(self) -> None:
        self.elapsed: float = 0.0
        self._start: float = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed = time.perf_counter() - self._start


def build_telemetry(
    *,
    node: str,
    role: str,
    summary: str,
    usage: Usage,
    duration_sec: float,
) -> TelemetryMsg:
    return TelemetryMsg(
        node=node,
        role=role,
        ts=now_iso(),
        summary=summary,
        tokens_in=usage.input_tokens,
        tokens_out=usage.output_tokens,
        cache_creation=usage.cache_creation_input_tokens,
        cache_read=usage.cache_read_input_tokens,
        cost_usd=usage.cost_usd,
        duration_sec=round(duration_sec, 2),
    )


def truncate(s: str, limit: int) -> str:
    """Return at most the last `limit` chars of `s`, with a marker if truncated."""
    if len(s) <= limit:
        return s
    return "…(truncated)…\n" + s[-limit:]
