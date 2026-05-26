"""End-to-end graph tests with a mocked Anthropic client.

Validates the full PM → Architect → Coder → QA pipeline including the
self-heal loop, without requiring an ANTHROPIC_API_KEY. The pytest
subprocess sandbox runs for real on whatever code the (mocked) Coder
writes, so we exercise:

- LangGraph topology + conditional edge routing
- Tool-use loop in the Coder node (write_file)
- Workspace path-safety + file write
- Sandbox actually executing pytest
- QA JSON parsing (mocked Haiku response)
- State accumulation (artifacts, messages, cost)
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from devswarm.config import load_config
from devswarm.graph import build_graph
from devswarm.state import initial_state
from devswarm.workspace import WorkspaceManager


# ──────────────────────────────────────────────────────────────────────────
# Fake-response builders
# ──────────────────────────────────────────────────────────────────────────


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, input_dict: dict, id_: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_dict)


def _usage(in_: int = 200, out: int = 100, cc: int = 0, cr: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=in_,
        output_tokens=out,
        cache_creation_input_tokens=cc,
        cache_read_input_tokens=cr,
    )


def _response(
    blocks: list[SimpleNamespace],
    stop_reason: str = "end_turn",
    usage: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage or _usage())


def _make_mock_client(scripted_responses: list[SimpleNamespace]) -> MagicMock:
    """Returns a MagicMock that yields responses in order, raising StopIteration if exhausted."""
    it = iter(scripted_responses)
    client = MagicMock()
    client.messages.create = MagicMock(side_effect=lambda **_: next(it))
    return client


# ──────────────────────────────────────────────────────────────────────────
# Helper: a trivial passing module + test
# ──────────────────────────────────────────────────────────────────────────

_PASS_MODULE = "def greet():\n    return 'hi'\n"
_PASS_TEST = (
    "from hello import greet\n\n"
    "def test_greet_returns_hi():\n"
    "    assert greet() == 'hi'\n"
)
_FAIL_MODULE = "def greet():\n    return 'WRONG'\n"


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


def test_full_pipeline_passes_first_try(tmp_path: Path):
    """PM → Architect → Coder → QA → END, all green on first iteration."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    ws = WorkspaceManager.create(workspace_root, "test01", "Build greet()")

    scripted = [
        # PM
        _response([_text_block("# PRD: hello\n\n## Goal\nGreet the world.")]),
        # Architect
        _response(
            [
                _text_block(
                    "## Architecture Spec\nSingle module `hello.py`.\n"
                    "\n## Security Constraints\n1. No eval.\n2. Pure function only."
                )
            ]
        ),
        # Coder turn 1 — writes both files
        _response(
            [
                _text_block("Plan: write trivial module + matching test."),
                _tool_use_block("write_file", {"path": "hello.py", "content": _PASS_MODULE}, id_="t1"),
                _tool_use_block("write_file", {"path": "test_hello.py", "content": _PASS_TEST}, id_="t2"),
            ],
            stop_reason="tool_use",
        ),
        # Coder turn 2 — model decides it's done after seeing tool_results
        _response([_text_block("Files written.")], stop_reason="end_turn"),
        # No QA LLM call: tests will pass → QA shortcuts the LLM
    ]
    client = _make_mock_client(scripted)

    cfg = load_config(workspace_root=workspace_root)
    cfg = replace(cfg, max_retries=0)

    app = build_graph(client, cfg)
    seed = initial_state(
        task_id="test01",
        user_request="Build greet()",
        workspace_path=str(ws.root),
        max_heal_iters=cfg.max_heal_iters,
    )

    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["tests_passed"] is True
    assert final["heal_iter"] == 1
    assert (ws.root / "hello.py").exists()
    assert (ws.root / "test_hello.py").exists()

    roles = [m["role"] for m in final["messages"]]
    assert roles == ["pm", "architect", "coder", "qa"]

    # Two artifacts written by Coder.
    artifact_paths = {a["path"] for a in final["artifacts"]}
    assert artifact_paths == {"hello.py", "test_hello.py"}

    # Cost should be non-zero from PM/Architect/Coder (QA skipped LLM on pass).
    assert final["cost_estimate_usd"] > 0


def test_self_heal_recovers_after_one_failure(tmp_path: Path):
    """Coder writes broken code, QA fails, loop routes back, Coder fixes, END."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    ws = WorkspaceManager.create(workspace_root, "test02", "Build greet()")

    qa_failure_json = (
        '{"passed": false, "exit_code": 1, '
        '"failed_tests": ["test_hello.py::test_greet_returns_hi"], '
        '"root_cause": "Function returns WRONG, expected hi.", '
        '"fix_direction": "Change greet() to return \'hi\'.", '
        '"stdout_tail": "FAILED test_hello.py::test_greet_returns_hi", '
        '"stderr_tail": ""}'
    )

    scripted = [
        # PM
        _response([_text_block("# PRD: hello\n## Goal\nGreet.")]),
        # Architect
        _response(
            [_text_block("## Architecture Spec\nx\n## Security Constraints\n1. None.")]
        ),
        # Coder iter 1 — writes broken module + correct test
        _response(
            [
                _tool_use_block("write_file", {"path": "hello.py", "content": _FAIL_MODULE}, id_="t1"),
                _tool_use_block("write_file", {"path": "test_hello.py", "content": _PASS_TEST}, id_="t2"),
            ],
            stop_reason="tool_use",
        ),
        _response([_text_block("Wrote files.")], stop_reason="end_turn"),
        # QA Haiku diagnoses the failure
        _response([_text_block(qa_failure_json)]),
        # Coder iter 2 — heal mode, writes fix
        _response(
            [
                _tool_use_block("write_file", {"path": "hello.py", "content": _PASS_MODULE}, id_="t3"),
            ],
            stop_reason="tool_use",
        ),
        _response([_text_block("Fixed.")], stop_reason="end_turn"),
        # No QA LLM call needed: pytest will pass this time
    ]
    client = _make_mock_client(scripted)

    cfg = load_config(workspace_root=workspace_root)
    cfg = replace(cfg, max_retries=0)

    app = build_graph(client, cfg)
    seed = initial_state(
        task_id="test02",
        user_request="Build greet()",
        workspace_path=str(ws.root),
        max_heal_iters=cfg.max_heal_iters,
    )

    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["tests_passed"] is True
    assert final["heal_iter"] == 2  # 1 initial + 1 heal pass

    roles = [m["role"] for m in final["messages"]]
    assert roles.count("coder") == 2
    assert roles.count("qa") == 2

    # The latest file on disk is the fixed version.
    assert (ws.root / "hello.py").read_text() == _PASS_MODULE

    # The QA report on the first iteration drove the heal — its root_cause
    # was passed into Coder iter 2's user message. We can verify the call
    # sequence has the heal text by checking we used all 7 scripted responses.
    assert client.messages.create.call_count == 7


def test_max_heal_exhausted_terminates_with_failure(tmp_path: Path):
    """If Coder keeps writing broken code, graph terminates after max_heal_iters."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    ws = WorkspaceManager.create(workspace_root, "test03", "Build greet()")

    qa_failure_json = (
        '{"passed": false, "exit_code": 1, '
        '"failed_tests": ["test_hello.py::test_greet_returns_hi"], '
        '"root_cause": "still wrong", "fix_direction": "fix it", '
        '"stdout_tail": "", "stderr_tail": ""}'
    )

    # max_heal_iters = 2; expect 2 Coder + 2 QA cycles then END.
    # Each Coder pass uses 2 LLM calls (write_file batch + closing), each QA fail uses 1.
    # 2 (PM+Arch) + 2 * (2 Coder + 1 QA) = 8 calls. We supply 10 to be safe.
    def fail_cycle():
        return [
            _response(
                [
                    _tool_use_block(
                        "write_file", {"path": "hello.py", "content": _FAIL_MODULE}, id_="x1"
                    ),
                    _tool_use_block(
                        "write_file", {"path": "test_hello.py", "content": _PASS_TEST}, id_="x2"
                    ),
                ],
                stop_reason="tool_use",
            ),
            _response([_text_block("Wrote (still broken).")], stop_reason="end_turn"),
            _response([_text_block(qa_failure_json)]),
        ]

    scripted = (
        [_response([_text_block("# PRD")]), _response([_text_block("## Architecture\n## Security Constraints\n1. None.")])]
        + fail_cycle()
        + fail_cycle()
    )
    client = _make_mock_client(scripted)

    cfg = load_config(workspace_root=workspace_root)
    cfg = replace(cfg, max_retries=0, max_heal_iters=2)

    app = build_graph(client, cfg)
    seed = initial_state(
        task_id="test03",
        user_request="Build greet()",
        workspace_path=str(ws.root),
        max_heal_iters=cfg.max_heal_iters,
    )

    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["tests_passed"] is False
    assert final["heal_iter"] == 2  # exhausted limit
    assert final["qa_report"]["passed"] is False
    assert "root_cause" in final["qa_report"]
