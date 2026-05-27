"""DevSwarm agent system prompts.

This package owns all four agent system prompts plus the Coder heal
user-message template. Prompts here are designed to be **stable across runs**
so that the LLM wrapper can attach `cache_control: ephemeral` and benefit
from Anthropic prompt caching.

Token-floor targets (per Anthropic caching minimums):
- PM_SYSTEM, ARCHITECT_SYSTEM, CODER_SYSTEM : >= 1024 tokens (Opus / Sonnet floor)
- QA_SYSTEM                                  : >= 2048 tokens (Haiku 4.5 floor)
"""

from ._versions import (
    ARCHITECT_PROMPT_VERSION,
    CODER_PROMPT_VERSION,
    PM_PROMPT_VERSION,
    PROMPT_VERSIONS,
    QA_PROMPT_VERSION,
    get_version,
)
from .architect import ARCHITECT_SYSTEM
from .coder import CODER_HEAL_USER_TEMPLATE, CODER_SYSTEM
from .pm import PM_SYSTEM
from .qa import QA_SYSTEM

__all__ = [
    "ARCHITECT_PROMPT_VERSION",
    "ARCHITECT_SYSTEM",
    "CODER_HEAL_USER_TEMPLATE",
    "CODER_PROMPT_VERSION",
    "CODER_SYSTEM",
    "PM_PROMPT_VERSION",
    "PM_SYSTEM",
    "PROMPT_VERSIONS",
    "QA_PROMPT_VERSION",
    "QA_SYSTEM",
    "get_version",
]
