"""LangGraph topology for DevSwarm.

```
START → pm → architect → coder → reviewer ─┬─ qa ─┬─ done (END)
                          ▲          │       │      │
                          │          └───────┘      │ heal (if heal_iter < max && !tests_passed)
                          └──────────────────────────┘
```

Two back-edges feed the Coder, both expressed as conditional edges:
- `reviewer → coder` (review block): adversarial review failed; fix the findings
  before tests run. Approved files fall through to QA.
- `qa → coder` (QA heal): tests failed; fix what broke. Every Coder re-pass is
  re-reviewed before it is re-tested, so the cycle is coder → reviewer → qa.

See docs/02 §3 for the routing contract and §5.5 for the review gate.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from .config import Config
from .nodes import architect_node, coder_node, pm_node, qa_node, reviewer_node
from .state import SwarmState


def _over_budget(state: SwarmState) -> bool:
    """Budget guardrail: True if we'd cross the USD limit. 0.0 means unlimited."""
    limit = state.get("cost_limit_usd", 0.0)
    return limit > 0 and state.get("cost_estimate_usd", 0.0) >= limit


def _route_after_review(state: SwarmState) -> Literal["heal", "qa", "done"]:
    if state.get("review_passed", False):
        return "qa"  # approved → run the tests
    if state.get("review_iter", 0) >= state.get("max_review_iters", 3):
        return "done"  # review rounds exhausted → bail (don't waste QA)
    if _over_budget(state):
        return "done"
    return "heal"  # blocked → back to Coder with review_findings


def _route_after_qa(state: SwarmState) -> Literal["heal", "done"]:
    if state.get("tests_passed", False):
        return "done"
    if state.get("heal_iter", 0) >= state.get("max_heal_iters", 5):
        return "done"
    if _over_budget(state):
        return "done"
    return "heal"


def build_graph(client: Any, cfg: Config) -> Any:
    """Build and compile the LangGraph application with client/config closed over."""
    g: StateGraph = StateGraph(SwarmState)

    g.add_node("pm", partial(pm_node, client=client, cfg=cfg))
    g.add_node("architect", partial(architect_node, client=client, cfg=cfg))
    g.add_node("coder", partial(coder_node, client=client, cfg=cfg))
    g.add_node("reviewer", partial(reviewer_node, client=client, cfg=cfg))
    g.add_node("qa", partial(qa_node, client=client, cfg=cfg))

    g.add_edge(START, "pm")
    g.add_edge("pm", "architect")
    g.add_edge("architect", "coder")
    g.add_edge("coder", "reviewer")
    g.add_conditional_edges(
        "reviewer",
        _route_after_review,
        {
            "heal": "coder",
            "qa": "qa",
            "done": END,
        },
    )
    g.add_conditional_edges(
        "qa",
        _route_after_qa,
        {
            "heal": "coder",
            "done": END,
        },
    )

    return g.compile()
