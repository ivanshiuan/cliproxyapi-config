"""Runtime configuration for DevSwarm.

Reads from environment variables (loaded via python-dotenv in the CLI),
with safe defaults baked in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Immutable runtime config snapshot."""

    # Anthropic models — see the system prompt for canonical IDs.
    model_pm: str
    model_architect: str
    model_coder: str
    model_reviewer: str
    model_qa: str

    # Self-heal loop bound.
    max_heal_iters: int

    # Adversarial review loop bound.
    max_review_iters: int

    # Filesystem layout.
    workspace_root: Path

    # Sandbox.
    sandbox_timeout_sec: int

    # LLM call hygiene.
    request_timeout_sec: int
    max_retries: int

    # Hermes — outbound notification channel: "console" | "stub" | "none".
    notify_channel: str


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_config(workspace_root: Path | None = None) -> Config:
    """Build a Config from the current environment.

    `workspace_root` from the caller (e.g. CLI flag) takes precedence over the env var.
    """
    ws = workspace_root or Path(
        os.getenv("DEVSWARM_WORKSPACE_ROOT") or "./workspace"
    )
    return Config(
        model_pm=os.getenv("DEVSWARM_MODEL_PM", "claude-opus-4-7"),
        model_architect=os.getenv("DEVSWARM_MODEL_ARCHITECT", "claude-opus-4-7"),
        model_coder=os.getenv("DEVSWARM_MODEL_CODER", "claude-sonnet-4-6"),
        model_reviewer=os.getenv("DEVSWARM_MODEL_REVIEWER", "claude-opus-4-7"),
        model_qa=os.getenv("DEVSWARM_MODEL_QA", "claude-haiku-4-5-20251001"),
        max_heal_iters=_env_int("DEVSWARM_MAX_HEAL_ITERS", 5),
        max_review_iters=_env_int("DEVSWARM_MAX_REVIEW_ITERS", 3),
        workspace_root=ws.expanduser().resolve(),
        sandbox_timeout_sec=_env_int("DEVSWARM_SANDBOX_TIMEOUT", 60),
        request_timeout_sec=_env_int("DEVSWARM_REQUEST_TIMEOUT", 120),
        max_retries=_env_int("DEVSWARM_MAX_RETRIES", 2),
        notify_channel=(os.getenv("DEVSWARM_NOTIFY") or "console").strip().lower(),
    )


# Anthropic per-million-token pricing (USD) as of 2026-Q1.
# Used only for cost estimation; not authoritative — refresh from console.anthropic.com.
PRICING_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0,
        "output": 75.0,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.0,
        "output": 5.0,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
}


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Rough USD estimate. Returns 0.0 for unknown models."""
    rates = PRICING_USD_PER_MTOK.get(model)
    if not rates:
        return 0.0
    return (
        (input_tokens / 1_000_000) * rates["input"]
        + (output_tokens / 1_000_000) * rates["output"]
        + (cache_creation_tokens / 1_000_000) * rates["cache_write"]
        + (cache_read_tokens / 1_000_000) * rates["cache_read"]
    )
