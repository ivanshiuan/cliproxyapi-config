"""PM Agent node — turns the commander's raw request into a precise PRD."""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..llm import call as llm_call
from ..prompts import PM_SYSTEM
from ..state import SwarmState
from ._common import Timer, build_telemetry


def pm_node(state: SwarmState, *, client: Any, cfg: Config) -> dict[str, Any]:
    user_request = state["user_request"]
    user_msg = (
        "Here is the commander's task brief. Produce the PRD per your system prompt.\n\n"
        "--- BEGIN BRIEF ---\n"
        f"{user_request}\n"
        "--- END BRIEF ---"
    )

    with Timer() as t:
        resp = llm_call(
            client,
            cfg,
            model=cfg.model_pm,
            system=PM_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=4096,
            temperature=0.2,
            cache_system=True,
        )

    prd_text = resp.text or "(PM returned empty PRD)"
    summary = f"PRD generated ({len(prd_text)} chars)"
    telemetry = build_telemetry(
        node="pm",
        role="pm",
        summary=summary,
        usage=resp.usage,
        duration_sec=t.elapsed,
    )

    return {
        "prd": prd_text,
        "messages": [telemetry],
        "cost_estimate_usd": state.get("cost_estimate_usd", 0.0) + resp.usage.cost_usd,
    }
