"""End-to-end graph tests with a mocked Anthropic client.

Validates the full PM → Architect → Coder → Reviewer → QA pipeline including
both feedback loops (Reviewer block → Coder, QA fail → Coder), without
requiring an ANTHROPIC_API_KEY. The pytest subprocess sandbox runs for real on
whatever code the (mocked) Coder writes, so we exercise:

- LangGraph topology + both conditional edges (route_after_review, route_after_qa)
- The adversarial Reviewer gate sitting before QA
- Tool-use loop in the Coder node (write_file)
- Workspace path-safety + file write
- Sandbox actually executing pytest
- QA + Reviewer JSON parsing (mocked responses)
- State accumulation (artifacts, messages, cost, review_iter, heal_iter)
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
# Helper: a trivial passing module + test, and the Reviewer verdicts
# ──────────────────────────────────────────────────────────────────────────

_PASS_MODULE = "def greet():\n    return 'hi'\n"
_PASS_TEST = (
    "from hello import greet\n\n"
    "def test_greet_returns_hi():\n"
    "    assert greet() == 'hi'\n"
)
_FAIL_MODULE = "def greet():\n    return 'WRONG'\n"

_REVIEW_PASS_JSON = '{"verdict": "PASS", "findings": [], "advisory": []}'
_REVIEW_BLOCK_JSON = (
    '{"verdict": "BLOCK", "findings": [{"severity": "CRITICAL", '
    '"location": "hello.py:greet", "issue": "Returns WRONG, not the required '
    'greeting.", "basis": "AC1"}], "advisory": []}'
)


def _review_pass(usage: SimpleNamespace | None = None) -> SimpleNamespace:
    return _response([_text_block(_REVIEW_PASS_JSON)], usage=usage)


def _review_block(usage: SimpleNamespace | None = None) -> SimpleNamespace:
    return _response([_text_block(_REVIEW_BLOCK_JSON)], usage=usage)


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


def test_full_pipeline_passes_first_try(tmp_path: Path):
    """PM → Architect → Coder → Reviewer → QA → END, all green on first pass."""
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
        # Reviewer approves
        _review_pass(),
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
        max_review_iters=cfg.max_review_iters,
    )

    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["tests_passed"] is True
    assert final["heal_iter"] == 1
    assert final["review_iter"] == 1
    assert final["review_passed"] is True
    assert (ws.root / "hello.py").exists()
    assert (ws.root / "test_hello.py").exists()

    roles = [m["role"] for m in final["messages"]]
    assert roles == ["pm", "architect", "coder", "reviewer", "qa"]

    # Two artifacts written by Coder.
    artifact_paths = {a["path"] for a in final["artifacts"]}
    assert artifact_paths == {"hello.py", "test_hello.py"}

    # Cost should be non-zero from PM/Architect/Coder/Reviewer (QA skipped LLM).
    assert final["cost_estimate_usd"] > 0


def test_reviewer_blocks_then_coder_fixes(tmp_path: Path):
    """Reviewer BLOCKs broken code, Coder fixes via review-heal, Reviewer PASS, QA green."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    ws = WorkspaceManager.create(workspace_root, "test05", "Build greet()")

    scripted = [
        # PM
        _response([_text_block("# PRD: hello\n## Goal\nGreet. AC1: greet() == 'hi'.")]),
        # Architect
        _response([_text_block("## Architecture Spec\nx\n## Security Constraints\n1. None.")]),
        # Coder iter 1 — writes BROKEN module + correct test
        _response(
            [
                _tool_use_block("write_file", {"path": "hello.py", "content": _FAIL_MODULE}, id_="t1"),
                _tool_use_block("write_file", {"path": "test_hello.py", "content": _PASS_TEST}, id_="t2"),
            ],
            stop_reason="tool_use",
        ),
        _response([_text_block("Wrote files.")], stop_reason="end_turn"),
        # Reviewer BLOCKs the broken code (before pytest ever runs)
        _review_block(),
        # Coder iter 2 — review-heal mode, writes the fix
        _response(
            [
                _tool_use_block("write_file", {"path": "hello.py", "content": _PASS_MODULE}, id_="t3"),
            ],
            stop_reason="tool_use",
        ),
        _response([_text_block("Fixed per review.")], stop_reason="end_turn"),
        # Reviewer approves the fix
        _review_pass(),
        # QA: pytest passes → no QA LLM call
    ]
    client = _make_mock_client(scripted)

    cfg = load_config(workspace_root=workspace_root)
    cfg = replace(cfg, max_retries=0)

    app = build_graph(client, cfg)
    seed = initial_state(
        task_id="test05",
        user_request="Build greet()",
        workspace_path=str(ws.root),
        max_heal_iters=cfg.max_heal_iters,
        max_review_iters=cfg.max_review_iters,
    )

    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["tests_passed"] is True
    assert final["review_passed"] is True
    assert final["review_iter"] == 2  # blocked once, approved once
    assert final["heal_iter"] == 2  # fresh pass + one review-heal pass

    roles = [m["role"] for m in final["messages"]]
    # The block routed back to Coder BEFORE any QA ran; QA appears once at the end.
    assert roles == ["pm", "architect", "coder", "reviewer", "coder", "reviewer", "qa"]

    # The fix landed on disk and tests never ran on the broken version.
    assert (ws.root / "hello.py").read_text() == _PASS_MODULE
    assert client.messages.create.call_count == 8


def test_self_heal_recovers_after_one_failure(tmp_path: Path):
    """Reviewer passes, QA fails on a runtime bug, loop heals, Reviewer passes, END."""
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
        _response([_text_block("## Architecture Spec\nx\n## Security Constraints\n1. None.")]),
        # Coder iter 1 — writes broken module + correct test
        _response(
            [
                _tool_use_block("write_file", {"path": "hello.py", "content": _FAIL_MODULE}, id_="t1"),
                _tool_use_block("write_file", {"path": "test_hello.py", "content": _PASS_TEST}, id_="t2"),
            ],
            stop_reason="tool_use",
        ),
        _response([_text_block("Wrote files.")], stop_reason="end_turn"),
        # Reviewer lets it through (the defect here is one tests catch at runtime)
        _review_pass(),
        # QA Haiku diagnoses the failure
        _response([_text_block(qa_failure_json)]),
        # Coder iter 2 — QA-heal mode, writes fix
        _response(
            [
                _tool_use_block("write_file", {"path": "hello.py", "content": _PASS_MODULE}, id_="t3"),
            ],
            stop_reason="tool_use",
        ),
        _response([_text_block("Fixed.")], stop_reason="end_turn"),
        # Reviewer approves the fix
        _review_pass(),
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
        max_review_iters=cfg.max_review_iters,
    )

    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["tests_passed"] is True
    assert final["heal_iter"] == 2  # 1 initial + 1 heal pass
    assert final["review_iter"] == 2  # re-reviewed after the heal

    roles = [m["role"] for m in final["messages"]]
    assert roles.count("coder") == 2
    assert roles.count("reviewer") == 2
    assert roles.count("qa") == 2

    # The latest file on disk is the fixed version.
    assert (ws.root / "hello.py").read_text() == _PASS_MODULE

    # PM + Arch + (Coder x2 + Reviewer x1 + QA x1) + (Coder x2 + Reviewer x1) = 9 calls.
    assert client.messages.create.call_count == 9


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

    # max_heal_iters = 2; Reviewer passes each round so QA is what fails.
    # Each cycle: Coder(2 calls) + Reviewer(1) + QA(1) = 4 calls.
    # 2 (PM+Arch) + 2 * 4 = 10 calls.
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
            _review_pass(),
            _response([_text_block(qa_failure_json)]),
        ]

    scripted = [
        _response([_text_block("# PRD")]),
        _response([_text_block("## Architecture\n## Security Constraints\n1. None.")]),
        *fail_cycle(),
        *fail_cycle(),
    ]
    client = _make_mock_client(scripted)

    cfg = load_config(workspace_root=workspace_root)
    cfg = replace(cfg, max_retries=0, max_heal_iters=2)

    app = build_graph(client, cfg)
    seed = initial_state(
        task_id="test03",
        user_request="Build greet()",
        workspace_path=str(ws.root),
        max_heal_iters=cfg.max_heal_iters,
        max_review_iters=cfg.max_review_iters,
    )

    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["tests_passed"] is False
    assert final["heal_iter"] == 2  # exhausted limit
    assert final["qa_report"]["passed"] is False
    assert "root_cause" in final["qa_report"]


def test_review_exhausted_terminates_with_failure(tmp_path: Path):
    """If the Reviewer keeps blocking, the graph terminates after max_review_iters
    without ever reaching QA."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    ws = WorkspaceManager.create(workspace_root, "test06", "Build greet()")

    # max_review_iters = 2: Coder writes, Reviewer blocks, Coder rewrites,
    # Reviewer blocks again → review_iter hits 2 → END (exhausted). QA never runs.
    def coder_then_block():
        return [
            _response(
                [
                    _tool_use_block("write_file", {"path": "hello.py", "content": _FAIL_MODULE}, id_="b1"),
                    _tool_use_block("write_file", {"path": "test_hello.py", "content": _PASS_TEST}, id_="b2"),
                ],
                stop_reason="tool_use",
            ),
            _response([_text_block("Wrote (still flagged).")], stop_reason="end_turn"),
            _review_block(),
        ]

    scripted = [
        _response([_text_block("# PRD")]),
        _response([_text_block("## Architecture\n## Security Constraints\n1. None.")]),
        *coder_then_block(),
        *coder_then_block(),
    ]
    client = _make_mock_client(scripted)

    cfg = load_config(workspace_root=workspace_root)
    cfg = replace(cfg, max_retries=0, max_review_iters=2)

    app = build_graph(client, cfg)
    seed = initial_state(
        task_id="test06",
        user_request="Build greet()",
        workspace_path=str(ws.root),
        max_heal_iters=cfg.max_heal_iters,
        max_review_iters=cfg.max_review_iters,
    )

    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["review_passed"] is False
    assert final["review_iter"] == 2  # exhausted review budget
    assert final["tests_passed"] is False  # QA never ran

    roles = [m["role"] for m in final["messages"]]
    assert "qa" not in roles  # the gate never opened
    assert roles.count("reviewer") == 2
    assert roles.count("coder") == 2


def test_budget_guard_halts_before_max_heal(tmp_path: Path):
    """A tight USD budget halts the graph before max_heal_iters even though
    tests still fail. Status will read "budget halted", not "failed exhaustion"."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    ws = WorkspaceManager.create(workspace_root, "test04", "Build greet()")

    qa_failure_json = (
        '{"passed": false, "exit_code": 1, '
        '"failed_tests": ["test_hello.py::test_greet_returns_hi"], '
        '"root_cause": "broken", "fix_direction": "fix",'
        ' "stdout_tail": "", "stderr_tail": ""}'
    )

    # Each LLM call we hand back claims very high token usage so cost
    # explodes immediately: ~$2.25 per call, blowing the $0.05 budget
    # after the very first Coder iteration finishes.
    big_usage = _usage(in_=1_000_000, out=200_000, cc=600_000, cr=0)

    scripted = [
        # PM (cost should jump well past budget alone)
        _response([_text_block("# PRD")], usage=big_usage),
        # Architect (and again)
        _response(
            [_text_block("## Architecture Spec\nx\n## Security Constraints\n1. None.")],
            usage=big_usage,
        ),
        # Coder iter 1 — broken code
        _response(
            [
                _tool_use_block("write_file", {"path": "hello.py", "content": _FAIL_MODULE}, id_="t1"),
                _tool_use_block("write_file", {"path": "test_hello.py", "content": _PASS_TEST}, id_="t2"),
            ],
            stop_reason="tool_use",
            usage=big_usage,
        ),
        _response([_text_block("Wrote.")], stop_reason="end_turn", usage=big_usage),
        # Reviewer passes (review_passed is checked before budget, so we reach QA)
        _review_pass(usage=big_usage),
        # QA failure (Haiku call)
        _response([_text_block(qa_failure_json)], usage=big_usage),
        # No further calls expected — budget should halt the graph here.
    ]
    client = _make_mock_client(scripted)

    cfg = load_config(workspace_root=workspace_root)
    cfg = replace(cfg, max_retries=0, max_heal_iters=5)

    app = build_graph(client, cfg)
    seed = initial_state(
        task_id="test04",
        user_request="Build greet()",
        workspace_path=str(ws.root),
        max_heal_iters=cfg.max_heal_iters,
        max_review_iters=cfg.max_review_iters,
        cost_limit_usd=0.05,  # < single-call cost, halt after first QA
    )
    final = app.invoke(seed, config={"recursion_limit": 32})

    assert final["tests_passed"] is False
    # Heal iter 1 happened (Coder ran once), then Reviewer passed, then QA failed,
    # then budget routing kicked in BEFORE coder ran a second time.
    assert final["heal_iter"] == 1
    assert final["cost_estimate_usd"] >= 0.05
    # We should not have used the full 5 heal budget.
    assert final["heal_iter"] < cfg.max_heal_iters
