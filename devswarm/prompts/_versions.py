"""Prompt version registry.

Why this exists
---------------
When we tune a system prompt (PM, Architect, Coder, QA), every task run
afterward gets the new behaviour. Without a recorded version, we can't
later answer "did task X run on the old or new PM prompt?" — and we
can't rule out prompt change as the cause of a regression in artifact
quality.

How it works
------------
Each prompt file imports its version constant from here. The telemetry
written to ``SwarmState.messages`` includes the version for the prompt
that produced the turn. Bump the version when the prompt text changes
in a way you'd want to compare against historical runs.

Conventions
-----------
Semantic-style ``MAJOR.MINOR.PATCH``:
  - PATCH for whitespace / typo / formatting fixes
  - MINOR for changes that tighten or relax constraints (new AC
    requirements, new tool-use rules)
  - MAJOR for a full rewrite of the agent's role

When you bump a MAJOR, also note the rationale in CHANGELOG entries at
the top of the corresponding prompt file.
"""

from __future__ import annotations

PM_PROMPT_VERSION = "1.1.0"
ARCHITECT_PROMPT_VERSION = "1.1.0"
CODER_PROMPT_VERSION = "1.1.0"
QA_PROMPT_VERSION = "1.1.0"


PROMPT_VERSIONS: dict[str, str] = {
    "pm": PM_PROMPT_VERSION,
    "architect": ARCHITECT_PROMPT_VERSION,
    "coder": CODER_PROMPT_VERSION,
    "qa": QA_PROMPT_VERSION,
}


def get_version(role: str) -> str:
    """Look up the version string for an agent role. Unknown roles → '0.0.0'."""
    return PROMPT_VERSIONS.get(role, "0.0.0")


__all__ = [
    "ARCHITECT_PROMPT_VERSION",
    "CODER_PROMPT_VERSION",
    "PM_PROMPT_VERSION",
    "PROMPT_VERSIONS",
    "QA_PROMPT_VERSION",
    "get_version",
]
