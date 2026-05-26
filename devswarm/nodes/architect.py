"""Architect Agent node — turns PRD into architecture spec + security constraints."""

from __future__ import annotations

import re
from typing import Any

from ..config import Config
from ..llm import call as llm_call
from ..prompts import ARCHITECT_SYSTEM
from ..state import SwarmState
from ._common import Timer, build_telemetry

# Match a numbered list line: "1. text", "12) text", "- 3. text", etc.
_NUMBERED = re.compile(r"^\s*(?:[-*]\s*)?\d+[\.\)]\s+(.+)$", re.MULTILINE)


def _split_arch_and_constraints(text: str) -> tuple[str, list[str]]:
    """Split the LLM output into architecture-spec markdown and a list of constraint imperatives.

    Strategy: find the `## Security Constraints` heading (or variant); everything
    above is the arch spec; numbered items below are individual constraints.
    """
    # Case-insensitive split on the security heading.
    parts = re.split(
        r"^#{1,4}\s*Security\s+Constraints\s*$",
        text,
        maxsplit=1,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if len(parts) == 1:
        # No security section found — treat all as arch_spec, no constraints.
        return text.strip(), []

    arch_spec = parts[0].strip()
    constraints_section = parts[1]
    constraints = [m.group(1).strip() for m in _NUMBERED.finditer(constraints_section)]
    return arch_spec, constraints


def architect_node(state: SwarmState, *, client: Any, cfg: Config) -> dict[str, Any]:
    prd = state.get("prd") or "(empty PRD)"
    user_msg = (
        "Here is the PM's PRD. Produce the Architecture Spec and the "
        "Security Constraints per your system prompt.\n\n"
        "--- BEGIN PRD ---\n"
        f"{prd}\n"
        "--- END PRD ---"
    )

    with Timer() as t:
        resp = llm_call(
            client,
            cfg,
            model=cfg.model_architect,
            system=ARCHITECT_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=4096,
            temperature=0.2,
            cache_system=True,
        )

    arch_spec, constraints = _split_arch_and_constraints(resp.text)
    if not arch_spec:
        arch_spec = resp.text or "(empty arch spec)"

    summary = (
        f"Arch spec generated ({len(arch_spec)} chars), "
        f"{len(constraints)} security constraints"
    )
    telemetry = build_telemetry(
        node="architect",
        role="architect",
        summary=summary,
        usage=resp.usage,
        duration_sec=t.elapsed,
    )

    return {
        "arch_spec": arch_spec,
        "security_constraints": constraints,
        "messages": [telemetry],
        "cost_estimate_usd": state.get("cost_estimate_usd", 0.0) + resp.usage.cost_usd,
    }
