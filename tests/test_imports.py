"""Sanity check: every module in the devswarm package imports cleanly.

Catches typos, circular imports, and missing dependencies before they hit
production. Does NOT call any LLM — safe to run without an API key.
"""

from __future__ import annotations

import importlib

import pytest

_MODULES = [
    "devswarm",
    "devswarm.cli",
    "devswarm.config",
    "devswarm.graph",
    "devswarm.hermes",
    "devswarm.llm",
    "devswarm.sandbox",
    "devswarm.state",
    "devswarm.workspace",
    "devswarm.nodes",
    "devswarm.nodes.pm",
    "devswarm.nodes.architect",
    "devswarm.nodes.coder",
    "devswarm.nodes.reviewer",
    "devswarm.nodes.qa",
    "devswarm.prompts",
    "devswarm.prompts.pm",
    "devswarm.prompts.architect",
    "devswarm.prompts.coder",
    "devswarm.prompts.reviewer",
    "devswarm.prompts.qa",
]


@pytest.mark.parametrize("module_name", _MODULES)
def test_module_imports(module_name: str):
    importlib.import_module(module_name)


def test_all_system_prompts_meet_floor():
    """Cache floor sanity — Opus/Sonnet need >=1024 tokens, Haiku >=2048.

    Rough estimate: 1 token ≈ 4 chars of English. We check char length as a proxy.
    """
    from devswarm.prompts import (
        ARCHITECT_SYSTEM,
        CODER_SYSTEM,
        PM_SYSTEM,
        QA_SYSTEM,
        REVIEWER_SYSTEM,
    )

    # Opus/Sonnet floor: 1024 tokens ≈ 4096 chars (very rough; English ~4 chars/token).
    for name, text in [
        ("PM", PM_SYSTEM),
        ("ARCHITECT", ARCHITECT_SYSTEM),
        ("CODER", CODER_SYSTEM),
        ("REVIEWER", REVIEWER_SYSTEM),
    ]:
        assert len(text) >= 4096, f"{name}_SYSTEM too short for prompt cache floor: {len(text)} chars"

    # Haiku floor: 2048 tokens ≈ 8192 chars.
    assert len(QA_SYSTEM) >= 8192, f"QA_SYSTEM too short for Haiku cache floor: {len(QA_SYSTEM)} chars"


def test_heal_template_has_all_placeholders():
    from devswarm.prompts import CODER_HEAL_USER_TEMPLATE

    for placeholder in [
        "{heal_iter}",
        "{max_heal_iters}",
        "{qa_report_summary}",
        "{failed_tests}",
        "{stdout_tail}",
        "{stderr_tail}",
        "{file_tree}",
    ]:
        assert placeholder in CODER_HEAL_USER_TEMPLATE, f"missing placeholder: {placeholder}"


def test_heal_template_formats_without_error():
    """All 7 placeholders accept arbitrary strings without IndexError/KeyError."""
    from devswarm.prompts import CODER_HEAL_USER_TEMPLATE

    result = CODER_HEAL_USER_TEMPLATE.format(
        heal_iter=1,
        max_heal_iters=5,
        qa_report_summary="sample",
        failed_tests="- test_x",
        stdout_tail="hello",
        stderr_tail="",
        file_tree="  a.py",
    )
    assert "Iteration 1 of 5" in result


def test_review_heal_template_formats_without_error():
    """The review-heal template's placeholders accept arbitrary strings."""
    from devswarm.prompts import CODER_REVIEW_HEAL_USER_TEMPLATE

    for placeholder in ["{review_iter}", "{max_review_iters}", "{review_findings}", "{file_tree}"]:
        assert placeholder in CODER_REVIEW_HEAL_USER_TEMPLATE, f"missing placeholder: {placeholder}"

    result = CODER_REVIEW_HEAL_USER_TEMPLATE.format(
        review_iter=1,
        max_review_iters=3,
        review_findings="1. [CRITICAL] x",
        file_tree="  a.py",
    )
    assert "round 1 of 3" in result


def test_prompt_versions_registered_for_all_roles():
    """E7: every role must have a version string so telemetry can pin it."""
    from devswarm.prompts import PROMPT_VERSIONS, get_version

    assert set(PROMPT_VERSIONS) == {"pm", "architect", "coder", "reviewer", "qa"}
    for role in ("pm", "architect", "coder", "reviewer", "qa"):
        assert get_version(role)  # non-empty


def test_get_version_unknown_role_returns_zero():
    from devswarm.prompts import get_version

    assert get_version("nonexistent") == "0.0.0"
