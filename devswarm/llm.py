"""Thin Anthropic SDK wrapper for DevSwarm.

Goals:
- Single `call()` primitive for one-shot LLM calls with system-prompt caching.
- `tool_loop()` for agentic loops (used by the Coder node) — drives
  tool_use → tool_result rounds until the model returns end_turn or a step cap.
- Usage accounting (input / output / cache-create / cache-read) returned with every
  response so cost tracking lives in one place.

Caching contract:
- The system prompt is always wrapped as a single text block with
  `cache_control: {"type": "ephemeral"}`. Hits last ~5 minutes; the self-heal
  loop reuses the same Coder system prompt across iterations, so iterations 2-5
  pay cache-read prices (~90% cheaper).
- Caller is responsible for keeping the system prompt long enough to clear the
  per-model minimum (Opus/Sonnet: 1024 tokens; Haiku: 2048 tokens). Under the
  minimum, the API silently skips caching — no error.

Retry policy:
- Up to `Config.max_retries` retries on transient errors (timeout, 5xx, rate limit).
- Exponential backoff: 2s, 4s, 8s. Capped.
- No retry on 4xx other than 429.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic import APIError, APIStatusError, APITimeoutError, RateLimitError

from .config import Config, estimate_cost_usd

# Newer Anthropic models (Opus 4.7+, Fable) deprecate the `temperature`
# parameter and the API returns a 400 if it is sent. Older models
# (Sonnet 4.6, Haiku 4.5) still accept it. We only forward `temperature`
# for models known to support it.
_TEMPERATURE_UNSUPPORTED_PREFIXES = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-fable-",
)


def _model_accepts_temperature(model: str) -> bool:
    """Return True if the model still accepts the `temperature` parameter."""
    return not model.startswith(_TEMPERATURE_UNSUPPORTED_PREFIXES)


@dataclass
class Usage:
    """Accumulated token usage across one or more LLM calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cost_usd += other.cost_usd


@dataclass
class LLMResponse:
    """Structured one-shot LLM response with usage."""

    text: str  # concatenated text blocks (empty if pure tool_use turn)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{id, name, input}]
    stop_reason: str = ""
    raw_content: list[dict[str, Any]] = field(default_factory=list)  # original block list
    usage: Usage = field(default_factory=Usage)


def _build_system_blocks(system: str, cache: bool) -> list[dict[str, Any]]:
    block: dict[str, Any] = {"type": "text", "text": system}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def _extract_usage(model: str, raw_usage: Any) -> Usage:
    input_tokens = getattr(raw_usage, "input_tokens", 0) or 0
    output_tokens = getattr(raw_usage, "output_tokens", 0) or 0
    cache_creation = getattr(raw_usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(raw_usage, "cache_read_input_tokens", 0) or 0
    cost = estimate_cost_usd(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
    )
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        cost_usd=cost,
    )


def _content_block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an SDK content block into a plain dict for state/serialization."""
    # The SDK exposes pydantic-ish models; .model_dump() works on recent versions.
    if hasattr(block, "model_dump"):
        return block.model_dump()
    # Fallback for older versions.
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}),
        }
    return {"type": btype or "unknown"}


def _parse_response(model: str, response: Any) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    raw_content: list[dict[str, Any]] = []

    for block in response.content:
        d = _content_block_to_dict(block)
        raw_content.append(d)
        if d.get("type") == "text":
            text_parts.append(d.get("text", ""))
        elif d.get("type") == "tool_use":
            tool_calls.append(
                {"id": d.get("id"), "name": d.get("name"), "input": d.get("input", {})}
            )

    return LLMResponse(
        text="".join(text_parts).strip(),
        tool_calls=tool_calls,
        stop_reason=getattr(response, "stop_reason", "") or "",
        raw_content=raw_content,
        usage=_extract_usage(model, response.usage),
    )


# ─────────────────────────────────────────────────────────────────────────
# Client construction
# ─────────────────────────────────────────────────────────────────────────


def make_client(cfg: Config) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        timeout=cfg.request_timeout_sec,
        max_retries=0,  # we handle retries ourselves for backoff visibility
    )


# ─────────────────────────────────────────────────────────────────────────
# One-shot call
# ─────────────────────────────────────────────────────────────────────────


def call(
    client: anthropic.Anthropic,
    cfg: Config,
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    cache_system: bool = True,
) -> LLMResponse:
    """Single Messages API call with prompt caching and bounded retries."""
    last_err: Exception | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "system": _build_system_blocks(system, cache_system),
                "messages": messages,
            }
            if _model_accepts_temperature(model):
                kwargs["temperature"] = temperature
            if tools:
                kwargs["tools"] = tools
            response = client.messages.create(**kwargs)
            return _parse_response(model, response)
        except (APITimeoutError, RateLimitError) as e:
            last_err = e
        except APIStatusError as e:
            # Retry 5xx; raise on 4xx other than 429 (caught above).
            if 500 <= e.status_code < 600:
                last_err = e
            else:
                raise
        except APIError as e:
            last_err = e

        if attempt < cfg.max_retries:
            backoff = 2 ** (attempt + 1)  # 2, 4, 8 ...
            time.sleep(min(backoff, 16))

    assert last_err is not None
    raise last_err


# ─────────────────────────────────────────────────────────────────────────
# Agentic loop (used by the Coder node)
# ─────────────────────────────────────────────────────────────────────────


ToolExecutor = Callable[[str, dict[str, Any]], str]
"""Signature: (tool_name, tool_input) -> stringified tool_result content."""


@dataclass
class ToolLoopResult:
    """Aggregated outcome of an agentic loop."""

    final_text: str
    steps: int
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""


def tool_loop(
    client: anthropic.Anthropic,
    cfg: Config,
    *,
    model: str,
    system: str,
    initial_user_message: str,
    tools: list[dict[str, Any]],
    executor: ToolExecutor,
    max_tokens: int = 8192,
    temperature: float = 0.2,
    max_steps: int = 10,
) -> ToolLoopResult:
    """Drive a tool-use loop until the model stops calling tools or max_steps reached.

    Conversation grows: user → assistant (text + tool_use) → user (tool_result(s)) → ...

    All system prompts are cached. The conversation messages are not cached
    (they change every step).
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": initial_user_message}
    ]
    result = ToolLoopResult(final_text="", steps=0)

    for step in range(max_steps):
        result.steps = step + 1
        resp = call(
            client,
            cfg,
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_system=True,
        )
        result.usage.add(resp.usage)
        result.stop_reason = resp.stop_reason

        # Append assistant turn verbatim (raw content blocks).
        messages.append({"role": "assistant", "content": resp.raw_content})

        if resp.stop_reason != "tool_use" or not resp.tool_calls:
            # Done: text-only turn or end_turn / stop_sequence / max_tokens.
            result.final_text = resp.text
            return result

        # Execute each tool call; collect results into a single user turn.
        tool_results: list[dict[str, Any]] = []
        for tc in resp.tool_calls:
            name = tc["name"]
            input_obj = tc.get("input") or {}
            try:
                output = executor(name, input_obj)
                is_error = False
            except Exception as e:  # surface to the model so it can recover
                output = f"ERROR: {type(e).__name__}: {e}"
                is_error = True
            result.tool_invocations.append(
                {"name": name, "input": input_obj, "output": output, "is_error": is_error}
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": output,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    # Hit max_steps without natural termination — still return what we have.
    return result
