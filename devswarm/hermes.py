"""Hermes — DevSwarm's outbound notification / escalation layer.

The swarm finishes (or gets stuck) inside the CLI. Hermes turns a terminal
``SwarmState`` into a single human-facing event and pushes it through a
pluggable channel, so a commander who is not watching the terminal still learns
that a task succeeded, failed, exhausted its retry budgets, or was halted on
cost.

Design:
- ``SwarmEvent`` is a flat, serializable summary of a terminal run.
- ``build_event`` classifies a final ``SwarmState`` into exactly one event kind.
- ``Notifier`` is a tiny protocol; v1 ships ``ConsoleNotifier`` (default),
  ``StubNotifier`` (captures events for tests), and ``NullNotifier`` (silent).
- ``make_notifier`` selects an implementation from the configured channel.

A real LINE / Slack / webhook adapter is intentionally NOT included here: it
needs a channel decision and credentials. It plugs in as one more ``Notifier``
implementation behind ``make_notifier`` without touching any call site.

Hermes must never crash a run. Call sites wrap ``notify`` so a delivery failure
degrades to a warning, never an exception that loses the artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# Event kinds, in rough order of "how much it needs your attention".
EVENT_KINDS = (
    "succeeded",
    "failed",
    "heal_exhausted",
    "review_exhausted",
    "budget_halted",
)

# Terminal kinds where a human should probably step in.
ESCALATION_KINDS = frozenset({"heal_exhausted", "review_exhausted", "budget_halted"})


@dataclass(frozen=True)
class SwarmEvent:
    """A flat, human-facing summary of a terminal DevSwarm run."""

    kind: str  # one of EVENT_KINDS
    task_id: str
    summary: str  # one-line headline (human language)
    detail: str  # 1-3 lines of context (root cause / findings / cost)
    workspace: str  # absolute path to the run's workspace
    cost_usd: float
    heal_iter: int
    review_iter: int

    @property
    def is_success(self) -> bool:
        return self.kind == "succeeded"

    @property
    def needs_attention(self) -> bool:
        return self.kind in ESCALATION_KINDS or self.kind == "failed"


def _classify(state: dict[str, Any]) -> str:
    """Map a terminal SwarmState to exactly one event kind.

    Mirrors the routing/teardown logic: success wins; otherwise a tight budget
    that has been crossed is reported as a halt (it pre-empts the retry loops);
    otherwise we report whichever retry budget was exhausted; else a generic
    failure.
    """
    if state.get("tests_passed", False):
        return "succeeded"

    limit = state.get("cost_limit_usd", 0.0)
    cost = state.get("cost_estimate_usd", 0.0)
    if limit > 0 and cost >= limit:
        return "budget_halted"

    review_iter = state.get("review_iter", 0)
    max_review = state.get("max_review_iters", 3)
    if not state.get("review_passed", False) and review_iter >= max_review:
        return "review_exhausted"

    if state.get("heal_iter", 0) >= state.get("max_heal_iters", 5):
        return "heal_exhausted"

    return "failed"


def _detail_for(kind: str, state: dict[str, Any]) -> str:
    cost = state.get("cost_estimate_usd", 0.0)
    limit = state.get("cost_limit_usd", 0.0)

    if kind == "succeeded":
        artifacts = state.get("artifacts") or []
        n = len({a.get("path") for a in artifacts if isinstance(a, dict)})
        return f"{n} artifact(s) written; tests green. Est. ${cost:.4f}."

    if kind == "budget_halted":
        return (
            f"Halted on cost: est. ${cost:.4f} reached the ${limit:.2f} limit "
            "before the next iteration. Raise --budget or narrow the task."
        )

    if kind == "review_exhausted":
        findings = (state.get("review_findings") or "(no findings text)").strip()
        head = findings.splitlines()[0] if findings else "(no findings)"
        return (
            "Adversarial review kept blocking; review rounds exhausted before "
            f"tests ran. Latest finding: {head}"
        )

    if kind == "heal_exhausted":
        qa = state.get("qa_report") or {}
        return (
            "Self-heal exhausted with tests still failing. Root cause: "
            f"{qa.get('root_cause', '(unavailable)')}"
        )

    # failed
    qa = state.get("qa_report") or {}
    return f"Run did not pass. Root cause: {qa.get('root_cause', '(unavailable)')}"


_SUMMARY = {
    "succeeded": "DevSwarm task {task_id} succeeded ✅",
    "failed": "DevSwarm task {task_id} failed ❌",
    "heal_exhausted": "DevSwarm task {task_id} exhausted its heal budget ⚠️",
    "review_exhausted": "DevSwarm task {task_id} exhausted its review budget ⚠️",
    "budget_halted": "DevSwarm task {task_id} halted on cost 💰",
}


def build_event(state: dict[str, Any], task_id: str, workspace: str) -> SwarmEvent:
    """Construct a SwarmEvent from a terminal SwarmState snapshot."""
    kind = _classify(state)
    return SwarmEvent(
        kind=kind,
        task_id=task_id,
        summary=_SUMMARY[kind].format(task_id=task_id),
        detail=_detail_for(kind, state),
        workspace=workspace,
        cost_usd=float(state.get("cost_estimate_usd", 0.0)),
        heal_iter=int(state.get("heal_iter", 0)),
        review_iter=int(state.get("review_iter", 0)),
    )


# ──────────────────────────────────────────────────────────────────────────
# Notifiers
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Notifier(Protocol):
    """Anything that can deliver a SwarmEvent somewhere."""

    def notify(self, event: SwarmEvent) -> None: ...


_KIND_STYLE = {
    "succeeded": ("green", "✅"),
    "failed": ("red", "❌"),
    "heal_exhausted": ("yellow", "⚠️"),
    "review_exhausted": ("yellow", "⚠️"),
    "budget_halted": ("yellow", "💰"),
}


class ConsoleNotifier:
    """Print a single concise push line to the terminal via rich.

    This is deliberately terser than the CLI's full summary panel — it is the
    "notification" view, not the audit view.
    """

    def __init__(self, console: Any | None = None) -> None:
        if console is None:
            from rich.console import Console

            console = Console()
        self._console = console

    def notify(self, event: SwarmEvent) -> None:
        color, _icon = _KIND_STYLE.get(event.kind, ("cyan", "•"))
        self._console.print(
            f"[bold {color}]\\[hermes][/bold {color}] {event.summary}\n"
            f"  [dim]{event.detail}[/dim]\n"
            f"  [dim]workspace: {event.workspace}[/dim]"
        )


class StubNotifier:
    """Captures events in memory instead of sending them. For tests / dry runs."""

    def __init__(self) -> None:
        self.events: list[SwarmEvent] = []

    def notify(self, event: SwarmEvent) -> None:
        self.events.append(event)


class NullNotifier:
    """Drops every event. Use when notifications are disabled."""

    def notify(self, event: SwarmEvent) -> None:
        return None


def make_notifier(channel: str, *, console: Any | None = None) -> Notifier:
    """Select a Notifier from a channel name.

    Known channels: "console" (default), "stub", "none". Unknown values fall
    back to console so a typo never silences notifications unexpectedly.
    """
    normalized = (channel or "console").strip().lower()
    if normalized in ("none", "off", "silent"):
        return NullNotifier()
    if normalized == "stub":
        return StubNotifier()
    return ConsoleNotifier(console=console)
