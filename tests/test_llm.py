"""Tests for the LLM wrapper's resilience behaviors (no real API calls)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx

from devswarm.config import load_config
from devswarm.llm import call


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _temperature_400() -> anthropic.APIStatusError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req)
    return anthropic.APIStatusError(
        "temperature is deprecated for this model", response=resp, body=None
    )


def test_call_retries_without_temperature_on_400():
    """A 400 about `temperature` triggers a transparent retry with it dropped."""
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        if "temperature" in kwargs:
            raise _temperature_400()
        return _ok_response()

    client = MagicMock()
    client.messages.create = create

    cfg = replace(load_config(), max_retries=0)  # no ordinary retries
    resp = call(
        client, cfg, model="claude-opus-4-7", system="sys",
        messages=[{"role": "user", "content": "hi"}], temperature=0.0,
    )

    assert resp.text == "ok"
    assert len(calls) == 2, "should retry exactly once"
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]


def test_call_does_not_loop_forever_if_temperature_400_persists():
    """If dropping temperature doesn't help, we stop (no infinite loop)."""
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        raise _temperature_400()  # always fails, even without temperature

    client = MagicMock()
    client.messages.create = create

    cfg = replace(load_config(), max_retries=0)
    raised = False
    try:
        call(client, cfg, model="m", system="s",
             messages=[{"role": "user", "content": "x"}], temperature=0.0)
    except anthropic.APIStatusError:
        raised = True

    assert raised
    # First call (with temp) -> drop -> second call (without temp) still 400 ->
    # not retried again. Exactly two attempts.
    assert len(calls) == 2
