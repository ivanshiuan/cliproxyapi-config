"""LangGraph topology for DevSwarm.

```
START → pm → architect → coder → qa ─┬─ done (END)
                          ▲          │
                          └──────────┘ heal (if heal_iter < max && !tests_passed)
```

The single back-edge `qa → coder` is the only cycle; it is expressed as a
conditional edge per LangGraph requirements.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from .config import Config
from .nodes import architect_node, coder_node, pm_node, qa_node
from .state import SwarmState


def _route_after_qa(state: SwarmState) -> Literal["heal", "done"]:
    if state.get("tests_passed", False):
        return "done"
    if state.get("heal_iter", 0) >= state.get("max_heal_iters", 5):
        return "done"
    return "heal"


def build_graph(client: Any, cfg: Config) -> Any:
    """Build and compile the LangGraph application with client/config closed over."""
    g: StateGraph = StateGraph(SwarmState)

    g.add_node("pm", partial(pm_node, client=client, cfg=cfg))
    g.add_node("architect", partial(architect_node, client=client, cfg=cfg))
    g.add_node("coder", partial(coder_node, client=client, cfg=cfg))
    g.add_node("qa", partial(qa_node, client=client, cfg=cfg))

    g.add_edge(START, "pm")
    g.add_edge("pm", "architect")
    g.add_edge("architect", "coder")
    g.add_edge("coder", "qa")
    g.add_conditional_edges(
        "qa",
        _route_after_qa,
        {
            "heal": "coder",
            "done": END,
        },
    )

    return g.compile()
