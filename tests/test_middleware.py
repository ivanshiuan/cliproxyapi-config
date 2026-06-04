"""Tests for the request_context + structured-logging middleware."""

from __future__ import annotations

import json
import logging

import httpx

from restaurant_api.main import app
from restaurant_api.middleware import (
    JSONFormatter,
    configure_logging,
    get_context_tenant_id,
    get_request_id,
    set_context_tenant_id,
    set_request_id,
)
from restaurant_api.middleware.request_context import _REQUEST_ID, _TENANT_ID


async def test_request_id_echoed_in_response_header():
    """Client-supplied X-Request-Id should be echoed back unchanged."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/version", headers={"X-Request-Id": "test-rid-abc123"})
    assert r.status_code == 200
    assert r.headers["X-Request-Id"] == "test-rid-abc123"


async def test_request_id_minted_when_missing():
    """If client doesn't send X-Request-Id, middleware mints one."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/version")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-Id")
    assert rid is not None
    # UUIDv7 hex-with-dashes shape
    assert len(rid) == 36
    assert rid.count("-") == 4


def test_context_vars_isolate_between_tokens():
    """ContextVar.set returns a Token; reset restores prior value."""
    set_request_id(None)
    assert get_request_id() is None
    tok = _REQUEST_ID.set("rid-1")
    assert get_request_id() == "rid-1"
    _REQUEST_ID.reset(tok)
    assert get_request_id() is None


def test_tenant_context_var():
    set_context_tenant_id(None)
    assert get_context_tenant_id() is None
    tok = _TENANT_ID.set("tenant-X")
    assert get_context_tenant_id() == "tenant-X"
    _TENANT_ID.reset(tok)


def test_json_formatter_includes_context_vars():
    """When request_id is in the contextvar, every log record carries it."""
    set_request_id("rid-formatter-test")
    set_context_tenant_id("tenant-fmt-test")
    try:
        rec = logging.LogRecord(
            name="x", level=logging.INFO, pathname="x.py", lineno=1,
            msg="hello %s", args=("world",), exc_info=None,
        )
        out = JSONFormatter().format(rec)
        payload = json.loads(out)
        assert payload["msg"] == "hello world"
        assert payload["request_id"] == "rid-formatter-test"
        assert payload["tenant_id"] == "tenant-fmt-test"
        assert payload["level"] == "INFO"
    finally:
        set_request_id(None)
        set_context_tenant_id(None)


def test_json_formatter_redacts_sensitive_keys():
    """Anything matching {password,api_key,token,secret,authorization} → ***REDACTED***."""
    rec = logging.LogRecord(
        name="x", level=logging.INFO, pathname="x.py", lineno=1,
        msg="login", args=(), exc_info=None,
    )
    rec.password = "hunter2"
    rec.api_key = "sk-LEAK"
    rec.normal = "fine"
    payload = json.loads(JSONFormatter().format(rec))
    assert payload["password"] == "***REDACTED***"
    assert payload["api_key"] == "***REDACTED***"
    assert payload["normal"] == "fine"


def test_configure_logging_installs_json_handler():
    """After configure_logging, root logger writes JSON to its handler."""
    configure_logging("DEBUG")
    root = logging.getLogger()
    assert any(isinstance(h.formatter, JSONFormatter) for h in root.handlers)


async def test_middleware_logs_request_complete(caplog):
    """The middleware emits http.request.start + http.request.complete."""
    caplog.set_level(logging.INFO, logger="restaurant_api.access")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/version")
    assert r.status_code == 200
    msgs = {rec.message for rec in caplog.records}
    assert "http.request.start" in msgs
    assert "http.request.complete" in msgs
