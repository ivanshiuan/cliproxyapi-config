"""Coder Agent node — writes module + tests via Anthropic tool-use loop.

The Coder is the only agent in v1 that uses the agentic tool-use loop. It owns
three tools (write_file, read_file, list_files), each scoped to the task's
workspace directory via `WorkspaceManager`.

Three invocation modes:
- Fresh       (heal_iter == 0): receives PRD + Architecture Spec + Security Constraints
- Review-heal (Reviewer blocked): receives the Reviewer's findings and must fix them
- QA-heal     (tests failed):    receives the QA report + file tree and must fix what broke

The review-heal vs QA-heal discriminator is the `review_passed` flag: the
Reviewer only routes back to the Coder while `review_passed` is False, whereas a
QA failure can only occur after the Reviewer has already approved (review_passed
True). See docs/02 §3 (route_after_review) and §5.5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..llm import tool_loop
from ..prompts import (
    CODER_HEAL_USER_TEMPLATE,
    CODER_REVIEW_HEAL_USER_TEMPLATE,
    CODER_SYSTEM,
)
from ..state import Artifact, SwarmState
from ..workspace import WorkspaceError, WorkspaceManager
from ._common import Timer, build_telemetry, truncate

# ──────────────────────────────────────────────────────────────────────────
# Tool schemas — Anthropic tool-use input_schema
# ──────────────────────────────────────────────────────────────────────────

CODER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "write_file",
        "description": (
            "Write a file into the task's workspace directory. "
            "Path must be relative; absolute paths and '..' are rejected. "
            "Overwrites if the file exists. Creates parent dirs as needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path inside the workspace, e.g. 'profit_calc.py'.",
                },
                "content": {
                    "type": "string",
                    "description": "Full UTF-8 text contents of the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read an existing file from the workspace. "
            "Useful during heal iterations to see the current code before rewriting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path inside the workspace.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List all files currently present in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def _classify(path: str) -> str:
    name = Path(path).name
    if name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if path.endswith(".py"):
        return "code"
    if path.endswith(".md"):
        return "doc"
    if path.endswith((".sql", ".json", ".yaml", ".yml")):
        return "schema"
    return "doc"


def _make_executor(ws: WorkspaceManager, written: list[Artifact]) -> Any:
    """Build the tool executor closure. Captures `written` for the node to inspect after."""

    def execute(name: str, input_obj: dict[str, Any]) -> str:
        if name == "write_file":
            path = input_obj.get("path", "")
            content = input_obj.get("content", "")
            try:
                n_bytes = ws.write(path, content)
            except WorkspaceError as e:
                return json.dumps({"ok": False, "error": str(e)})
            written.append(Artifact(path=path, kind=_classify(path), bytes=n_bytes))  # type: ignore[typeddict-item]
            return json.dumps({"ok": True, "path": path, "bytes": n_bytes})

        if name == "read_file":
            path = input_obj.get("path", "")
            try:
                content = ws.read(path)
            except (WorkspaceError, FileNotFoundError) as e:
                return json.dumps({"ok": False, "error": str(e)})
            return json.dumps({"ok": True, "path": path, "content": content})

        if name == "list_files":
            return json.dumps({"ok": True, "files": ws.list_files()})

        return json.dumps({"ok": False, "error": f"unknown tool: {name}"})

    return execute


def _build_fresh_user_message(state: SwarmState) -> str:
    prd = state.get("prd") or "(empty PRD)"
    arch_spec = state.get("arch_spec") or "(empty arch spec)"
    constraints = state.get("security_constraints") or []
    constraints_block = (
        "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(constraints))
        or "  (no explicit constraints provided)"
    )

    return (
        "You are starting a fresh task. Produce the module file and the test file.\n\n"
        "## PRD\n"
        f"{prd}\n\n"
        "## Architecture Spec\n"
        f"{arch_spec}\n\n"
        "## Security Constraints\n"
        f"{constraints_block}\n\n"
        "Plan briefly (1-3 sentences), then issue `write_file` tool calls for both files, "
        "then end with a one-sentence summary. Do not ask questions."
    )


def _build_review_heal_user_message(state: SwarmState, ws: WorkspaceManager) -> str:
    findings = state.get("review_findings") or "(no findings text provided)"
    return CODER_REVIEW_HEAL_USER_TEMPLATE.format(
        review_iter=state.get("review_iter", 0),
        max_review_iters=state.get("max_review_iters", 3),
        review_findings=findings,
        file_tree=ws.tree(),
    )


def _build_heal_user_message(state: SwarmState, ws: WorkspaceManager) -> str:
    qa = state.get("qa_report") or {}
    failed = qa.get("failed_tests") or []
    failed_block = "\n".join(f"  - {ft}" for ft in failed) if failed else "  (none reported)"
    qa_summary = (
        f"root_cause: {qa.get('root_cause', '(missing)')}\n"
        f"fix_direction: {qa.get('fix_direction', '(missing)')}"
    )
    return CODER_HEAL_USER_TEMPLATE.format(
        heal_iter=state.get("heal_iter", 0),
        max_heal_iters=state.get("max_heal_iters", 5),
        qa_report_summary=qa_summary,
        failed_tests=failed_block,
        stdout_tail=truncate(qa.get("stdout_tail", ""), 3000),
        stderr_tail=truncate(qa.get("stderr_tail", ""), 1500),
        file_tree=ws.tree(),
    )


def coder_node(state: SwarmState, *, client: Any, cfg: Config) -> dict[str, Any]:
    workspace_path = Path(state["workspace_path"])
    ws = WorkspaceManager(workspace_path)
    heal_iter = state.get("heal_iter", 0)

    if heal_iter == 0:
        # First pass: build from the PRD + spec.
        user_msg = _build_fresh_user_message(state)
    elif not state.get("review_passed", False) and state.get("review_findings"):
        # Reviewer blocked the current files (tests have not run yet).
        user_msg = _build_review_heal_user_message(state, ws)
    else:
        # Reviewer already approved a prior pass and QA found failing tests.
        user_msg = _build_heal_user_message(state, ws)

    written: list[Artifact] = []
    executor = _make_executor(ws, written)

    with Timer() as t:
        result = tool_loop(
            client,
            cfg,
            model=cfg.model_coder,
            system=CODER_SYSTEM,
            initial_user_message=user_msg,
            tools=CODER_TOOLS,
            executor=executor,
            max_tokens=8192,
            temperature=0.2,
            max_steps=8,
        )

    coder_notes = result.final_text or "(no closing summary)"
    summary = (
        f"iter={heal_iter + 1}, steps={result.steps}, "
        f"files_written={len(written)}, stop={result.stop_reason}"
    )
    telemetry = build_telemetry(
        node="coder",
        role="coder",
        summary=summary,
        usage=result.usage,
        duration_sec=t.elapsed,
    )

    return {
        "artifacts": written,
        "coder_notes": coder_notes,
        "heal_iter": heal_iter + 1,
        "messages": [telemetry],
        "cost_estimate_usd": state.get("cost_estimate_usd", 0.0) + result.usage.cost_usd,
    }
