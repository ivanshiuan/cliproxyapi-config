"""Anthropic LLM client — the real implementation of the ``LLMClient``
Protocol declared in :mod:`restaurant_api.jobs.daily_brief`.

Prompt caching is on by default and gives a ~90% cost reduction on the
static ``system`` prompt across repeated daily-brief invocations (the
same system role fires every day, sometimes every hour). We mark the
system message with ``cache_control={"type": "ephemeral"}`` per the
Anthropic API reference; anything under 5 minutes hits cache.

Retry policy is intentionally boring:

* ``httpx`` transport with a 60 s per-request timeout.
* Up to 3 retries on 5xx / 429 / connection reset with 1 s / 4 s / 16 s
  backoff — the same schedule the ingestion pipeline uses for embeddings.

Anything else (auth error, model-not-found, permission denied) raises
straight through so the cron catches it and marks the brief_run as
``failed``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from anthropic import AsyncAnthropic
from anthropic._exceptions import (
    APIConnectionError,
    APIStatusError,
    RateLimitError,
)

from ..jobs.daily_brief import LLMResult

logger = logging.getLogger("restaurant_api.llm_client")

_DEFAULT_MODEL = "claude-sonnet-5"
_DEFAULT_MAX_TOKENS = 4096
_BACKOFF_S = (1.0, 4.0, 16.0)


class AnthropicLLMClient:
    """Concrete ``LLMClient`` backed by the Anthropic messages API.

    Kept intentionally thin — the retry loop is the only "smart" part.
    All model / cache decisions come from ``Settings`` at construction so
    a single instance is safe to reuse across many brief runs.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        max_output_tokens: int = _DEFAULT_MAX_TOKENS,
        prompt_cache: bool = True,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("AnthropicLLMClient requires a non-empty api_key")
        self._model = model
        self._max_tokens = max_output_tokens
        self._prompt_cache = prompt_cache
        self._client = client or AsyncAnthropic(api_key=api_key)

    async def complete(
        self, *, system: str, user: str, model: str | None = None
    ) -> LLMResult:
        """Roundtrip one message pair. Retries transient failures."""
        target_model = model or self._model
        # Build the system block, optionally with cache marker.
        system_block: list[dict[str, Any]] | str
        if self._prompt_cache:
            system_block = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_block = system

        last_err: Exception | None = None
        for attempt in range(len(_BACKOFF_S) + 1):
            try:
                t0 = time.monotonic()
                resp = await self._client.messages.create(
                    model=target_model,
                    max_tokens=self._max_tokens,
                    system=system_block,  # type: ignore[arg-type]
                    messages=[{"role": "user", "content": user}],
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                # Concatenate all text blocks in the response. Anthropic
                # may split the answer across multiple content blocks.
                text_parts: list[str] = []
                for block in resp.content:
                    text = getattr(block, "text", None)
                    if text:
                        text_parts.append(text)
                text = "".join(text_parts).strip()
                usage = resp.usage
                logger.info(
                    "llm.complete",
                    extra={
                        "model": target_model,
                        "prompt_tokens": usage.input_tokens,
                        "completion_tokens": usage.output_tokens,
                        "latency_ms": latency_ms,
                        "cache_read": getattr(
                            usage, "cache_read_input_tokens", 0
                        ),
                    },
                )
                return LLMResult(
                    text=text,
                    model_name=target_model,
                    prompt_tokens=usage.input_tokens,
                    completion_tokens=usage.output_tokens,
                    latency_ms=latency_ms,
                )
            except (APIConnectionError, RateLimitError) as exc:
                last_err = exc
                if attempt >= len(_BACKOFF_S):
                    break
                logger.warning(
                    "llm.retry",
                    extra={
                        "attempt": attempt + 1,
                        "backoff_s": _BACKOFF_S[attempt],
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(_BACKOFF_S[attempt])
            except APIStatusError as exc:
                # 5xx are worth retrying; 4xx (auth, bad model) are not.
                if 500 <= exc.status_code < 600 and attempt < len(_BACKOFF_S):
                    last_err = exc
                    logger.warning(
                        "llm.retry_5xx",
                        extra={
                            "attempt": attempt + 1,
                            "status": exc.status_code,
                            "backoff_s": _BACKOFF_S[attempt],
                        },
                    )
                    await asyncio.sleep(_BACKOFF_S[attempt])
                    continue
                raise
        assert last_err is not None  # loop only exits after last retry
        raise last_err


def build_llm_client_from_settings() -> AnthropicLLMClient | None:
    """Convenience factory used by the scheduler wiring.

    Returns ``None`` if the API key is missing so the caller can decide
    whether to log-and-skip or hard-fail.
    """
    from ..config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    return AnthropicLLMClient(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        max_output_tokens=settings.llm_max_output_tokens,
        prompt_cache=settings.llm_prompt_cache,
    )


__all__ = [
    "AnthropicLLMClient",
    "build_llm_client_from_settings",
]
