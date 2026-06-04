"""Structured-JSON logging setup.

Why JSON: every log line is now parseable by any aggregator (Loki, ELK,
CloudWatch). Adds request_id / tenant_id automatically via context-var
filter — service code just calls ``logger.info("order.created", extra={...})``
without threading the id manually.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import ClassVar

from .request_context import get_context_tenant_id, get_request_id

# Keys we never want to surface (avoid accidentally logging secrets passed via extra=)
_REDACT_KEYS = {"password", "api_key", "token", "secret", "authorization"}


class JSONFormatter(logging.Formatter):
    """Emit each record as a single-line JSON document.

    Includes context-var fields (request_id, tenant_id) automatically — no
    need to plumb them through every log call.
    """

    # Standard LogRecord attributes we'd never want to duplicate into the payload.
    _SKIP: ClassVar[set[str]] = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        rid = get_request_id()
        if rid:
            payload["request_id"] = rid
        tid = get_context_tenant_id()
        if tid:
            payload["tenant_id"] = tid

        # Anything passed via logger.info("...", extra={"foo": "bar"})
        for key, value in record.__dict__.items():
            if key in self._SKIP or key.startswith("_"):
                continue
            if key.lower() in _REDACT_KEYS:
                payload[key] = "***REDACTED***"
            else:
                payload[key] = _safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def _safe(value: object) -> object:
    """Coerce common non-JSON types to something serializable."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return str(value)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger.

    Idempotent — safe to call multiple times. The middleware imports this from
    main.py lifespan startup.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Replace any pre-existing stream handlers with our JSON one.
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler):
            root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Quiet the noisier libraries — they'll inherit the JSON handler but at
    # higher level so their internal chatter doesn't drown app logs.
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel("WARNING")
