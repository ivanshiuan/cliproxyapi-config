"""Tests for POST /admin/line/liff — one-time LIFF app registration.

The real LiffAdminClient class is monkeypatched to a stub here; its own HTTP
behaviour (list/create/ensure against a mocked LINE API) is covered
end-to-end in tests/test_line_integration.py. This file only exercises the
router's own logic: admin gate, missing-token validation, URL construction,
and upstream-error translation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]

from restaurant_api.api.auth import AdminPrincipal, require_admin
from restaurant_api.integrations.line import LiffApiError, RichMenuApiError, WebhookApiError
from restaurant_api.main import app
from restaurant_api.routers import line_setup

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[require_admin] = lambda: AdminPrincipal()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class _FakeLiffAdminClient:
    """Stand-in for LiffAdminClient — records constructor args, returns a
    scripted ensure_app result (or raises), and no-ops on close."""

    calls: ClassVar[list[dict[str, object]]] = []
    ensure_result: ClassVar[tuple[str, bool] | None] = ("FAKE-LIFF-ID", True)
    ensure_error: ClassVar[LiffApiError | None] = None

    def __init__(self, channel_access_token: str) -> None:
        self.channel_access_token = channel_access_token

    async def ensure_app(self, *, view_url: str, description: str = "") -> tuple[str, bool]:
        _FakeLiffAdminClient.calls.append({"view_url": view_url, "description": description})
        if _FakeLiffAdminClient.ensure_error is not None:
            raise _FakeLiffAdminClient.ensure_error
        assert _FakeLiffAdminClient.ensure_result is not None
        return _FakeLiffAdminClient.ensure_result

    async def aclose(self) -> None:
        pass


@pytest_asyncio.fixture(autouse=True)
def _reset_fake_client():
    _FakeLiffAdminClient.calls = []
    _FakeLiffAdminClient.ensure_result = ("FAKE-LIFF-ID", True)
    _FakeLiffAdminClient.ensure_error = None
    yield


async def test_liff_setup_requires_admin() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/admin/line/liff", json={})
    assert resp.status_code in (401, 403)


async def test_liff_setup_rejects_missing_token(client: httpx.AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(
        line_setup, "get_settings", lambda: SimpleNamespace(line_channel_access_token="")
    )
    resp = await client.post("/admin/line/liff", json={})
    assert resp.status_code == 422
    assert "LINE_CHANNEL_ACCESS_TOKEN" in resp.json()["error"]["message"]


async def test_liff_setup_success_builds_slug_url_and_liff_param(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(line_channel_access_token="real-token"),
    )
    monkeypatch.setattr(line_setup, "LiffAdminClient", _FakeLiffAdminClient)

    resp = await client.post(
        "/admin/line/liff",
        json={"slug": "grand-open", "base_url": "https://chouhutiger.onrender.com"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["liff_id"] == "FAKE-LIFF-ID"
    assert body["created"] is True
    assert body["view_url"] == "https://chouhutiger.onrender.com/demo/campaign/grand-open"
    assert body["wheel_url_with_liff"] == (
        "https://chouhutiger.onrender.com/demo/campaign/grand-open?liff=FAKE-LIFF-ID"
    )
    assert _FakeLiffAdminClient.calls == [
        {
            "view_url": "https://chouhutiger.onrender.com/demo/campaign/grand-open",
            "description": "開幕輪盤-grand-open",
        }
    ]


async def test_liff_setup_wraps_upstream_error_as_502(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(line_channel_access_token="real-token"),
    )
    _FakeLiffAdminClient.ensure_error = LiffApiError(400, "bad view url", "/apps")
    monkeypatch.setattr(line_setup, "LiffAdminClient", _FakeLiffAdminClient)

    resp = await client.post("/admin/line/liff", json={"base_url": "https://example.com"})
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["code"] == "UPSTREAM_ERROR"
    assert body["error"]["details"]["status"] == 400


# ── POST /admin/line/richmenu ────────────────────────────────────────────


class _FakeRichMenuAdminClient:
    """Stand-in for RichMenuAdminClient — same shape as _FakeLiffAdminClient."""

    calls: ClassVar[list[dict[str, object]]] = []
    ensure_result: ClassVar[tuple[str, bool] | None] = ("FAKE-RM-ID", False)
    ensure_error: ClassVar[RichMenuApiError | None] = None

    def __init__(self, channel_access_token: str) -> None:
        self.channel_access_token = channel_access_token

    async def ensure_richmenu(
        self, body: dict[str, object], image_bytes: bytes
    ) -> tuple[str, bool]:
        _FakeRichMenuAdminClient.calls.append(
            {"body": body, "image_len": len(image_bytes)}
        )
        if _FakeRichMenuAdminClient.ensure_error is not None:
            raise _FakeRichMenuAdminClient.ensure_error
        assert _FakeRichMenuAdminClient.ensure_result is not None
        return _FakeRichMenuAdminClient.ensure_result

    async def aclose(self) -> None:
        pass


@pytest_asyncio.fixture(autouse=True)
def _reset_fake_richmenu_client():
    _FakeRichMenuAdminClient.calls = []
    _FakeRichMenuAdminClient.ensure_result = ("FAKE-RM-ID", False)
    _FakeRichMenuAdminClient.ensure_error = None
    yield


async def test_richmenu_setup_requires_admin() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/admin/line/richmenu", json={})
    assert resp.status_code in (401, 403)


async def test_richmenu_setup_rejects_missing_token(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup, "get_settings", lambda: SimpleNamespace(line_channel_access_token="")
    )
    resp = await client.post("/admin/line/richmenu", json={})
    assert resp.status_code == 422
    assert "LINE_CHANNEL_ACCESS_TOKEN" in resp.json()["error"]["message"]


async def test_richmenu_setup_success_reads_real_png_and_builds_wheel_url(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(line_channel_access_token="real-token"),
    )
    monkeypatch.setattr(line_setup, "RichMenuAdminClient", _FakeRichMenuAdminClient)

    resp = await client.post(
        "/admin/line/richmenu",
        json={"slug": "grand-open", "base_url": "https://chouhutiger.onrender.com"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["richmenu_id"] == "FAKE-RM-ID"
    assert body["replaced_existing"] is False
    assert body["wheel_url"] == "https://chouhutiger.onrender.com/demo/campaign/grand-open"

    assert len(_FakeRichMenuAdminClient.calls) == 1
    call = _FakeRichMenuAdminClient.calls[0]
    # Real committed PNG must actually be read off disk (not a placeholder).
    assert call["image_len"] > 1000
    sent_body = call["body"]
    assert sent_body["size"] == {"width": 2500, "height": 843}  # type: ignore[index]
    assert len(sent_body["areas"]) == 2  # type: ignore[arg-type]
    for area in sent_body["areas"]:  # type: ignore[union-attr]
        assert area["action"]["uri"] == "https://chouhutiger.onrender.com/demo/campaign/grand-open"


async def test_richmenu_setup_missing_png_gives_clear_error(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(line_channel_access_token="real-token"),
    )
    monkeypatch.setattr(line_setup, "_LINE_ASSETS_DIR", line_setup._LINE_ASSETS_DIR / "does-not-exist")
    monkeypatch.setattr(line_setup, "RichMenuAdminClient", _FakeRichMenuAdminClient)

    resp = await client.post("/admin/line/richmenu", json={})
    assert resp.status_code == 422
    assert "render_richmenu_png.py" in resp.json()["error"]["message"]


async def test_richmenu_setup_wraps_upstream_error_as_502(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(line_channel_access_token="real-token"),
    )
    _FakeRichMenuAdminClient.ensure_error = RichMenuApiError(400, "bad image", "/richmenu")
    monkeypatch.setattr(line_setup, "RichMenuAdminClient", _FakeRichMenuAdminClient)

    resp = await client.post("/admin/line/richmenu", json={})
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["code"] == "UPSTREAM_ERROR"
    assert body["error"]["details"]["status"] == 400


# ── GET /admin/line/status ───────────────────────────────────────────────


class _FakeStatusLiffClient:
    apps: ClassVar[list[dict[str, object]]] = []

    def __init__(self, channel_access_token: str) -> None:
        pass

    async def list_apps(self) -> list[dict[str, object]]:
        return _FakeStatusLiffClient.apps

    async def aclose(self) -> None:
        pass


class _FakeStatusRichMenuClient:
    default_id: ClassVar[str | None] = None

    def __init__(self, channel_access_token: str) -> None:
        pass

    async def get_default_richmenu_id(self) -> str | None:
        return _FakeStatusRichMenuClient.default_id

    async def aclose(self) -> None:
        pass


class _FakeStatusWebhookClient:
    endpoint: ClassVar[str | None] = None
    active: ClassVar[bool] = False

    def __init__(self, channel_access_token: str) -> None:
        pass

    async def get_endpoint(self) -> tuple[str | None, bool]:
        return _FakeStatusWebhookClient.endpoint, _FakeStatusWebhookClient.active

    async def aclose(self) -> None:
        pass


@pytest_asyncio.fixture(autouse=True)
def _reset_status_fakes():
    _FakeStatusLiffClient.apps = []
    _FakeStatusRichMenuClient.default_id = None
    _FakeStatusWebhookClient.endpoint = None
    _FakeStatusWebhookClient.active = False
    yield


async def test_status_requires_admin() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/admin/line/status")
    assert resp.status_code in (401, 403)


async def test_status_all_ready(client: httpx.AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(
            line_channel_access_token="tok",
            line_channel_secret="sec",
            line_oa_add_friend_url="https://lin.ee/testxyz",
        ),
    )
    _FakeStatusLiffClient.apps = [
        {"liffId": "L-1", "view": {"url": "https://x.onrender.com/demo/campaign/grand-open"}}
    ]
    _FakeStatusRichMenuClient.default_id = "RM-1"
    _FakeStatusWebhookClient.endpoint = "https://x.onrender.com/line/webhook"
    _FakeStatusWebhookClient.active = True
    monkeypatch.setattr(line_setup, "LiffAdminClient", _FakeStatusLiffClient)
    monkeypatch.setattr(line_setup, "RichMenuAdminClient", _FakeStatusRichMenuClient)
    monkeypatch.setattr(line_setup, "WebhookAdminClient", _FakeStatusWebhookClient)

    resp = await client.get("/admin/line/status", params={"base_url": "https://x.onrender.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["liff_id"] == "L-1"
    assert body["default_richmenu_id"] == "RM-1"
    assert body["push_token_configured"] is True
    assert body["webhook_secret_configured"] is True
    assert body["webhook_endpoint_url"] == "https://x.onrender.com/line/webhook"
    assert body["webhook_endpoint_matches"] is True
    assert body["webhook_active"] is True
    assert body["door_qr_add_friend_url"] == "https://lin.ee/testxyz"


async def test_status_not_ready_lists_gaps(client: httpx.AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(
            line_channel_access_token="tok", line_channel_secret="", line_oa_add_friend_url=""
        ),
    )
    # No LIFF app matches, no default rich menu, no webhook endpoint set.
    monkeypatch.setattr(line_setup, "LiffAdminClient", _FakeStatusLiffClient)
    monkeypatch.setattr(line_setup, "RichMenuAdminClient", _FakeStatusRichMenuClient)
    monkeypatch.setattr(line_setup, "WebhookAdminClient", _FakeStatusWebhookClient)

    resp = await client.get("/admin/line/status", params={"base_url": "https://x.onrender.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert body["liff_app_registered"] is False
    assert body["richmenu_set_as_default"] is False
    assert body["webhook_secret_configured"] is False
    assert body["webhook_endpoint_matches"] is False
    assert body["door_qr_add_friend_url"] is None
    joined = " ".join(body["notes"])
    assert "LINE_CHANNEL_SECRET" in joined
    assert "POST /admin/line/liff" in joined
    assert "POST /admin/line/richmenu" in joined
    assert "POST /admin/line/webhook" in joined
    assert "LINE_OA_ADD_FRIEND_URL" in joined


async def test_status_endpoint_matches_but_toggle_off(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    """Endpoint URL already set correctly but "Use webhook" is still off —
    the one gap that has no public API, so status must call it out clearly."""
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(
            line_channel_access_token="tok", line_channel_secret="sec", line_oa_add_friend_url=""
        ),
    )
    _FakeStatusLiffClient.apps = [
        {"liffId": "L-1", "view": {"url": "https://x.onrender.com/demo/campaign/grand-open"}}
    ]
    _FakeStatusRichMenuClient.default_id = "RM-1"
    _FakeStatusWebhookClient.endpoint = "https://x.onrender.com/line/webhook"
    _FakeStatusWebhookClient.active = False
    monkeypatch.setattr(line_setup, "LiffAdminClient", _FakeStatusLiffClient)
    monkeypatch.setattr(line_setup, "RichMenuAdminClient", _FakeStatusRichMenuClient)
    monkeypatch.setattr(line_setup, "WebhookAdminClient", _FakeStatusWebhookClient)

    resp = await client.get("/admin/line/status", params={"base_url": "https://x.onrender.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert body["webhook_endpoint_matches"] is True
    assert body["webhook_active"] is False
    joined = " ".join(body["notes"])
    assert "Use webhook" in joined


async def test_status_no_token_reports_cleanly(client: httpx.AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(
            line_channel_access_token="", line_channel_secret="", line_oa_add_friend_url=""
        ),
    )
    resp = await client.get("/admin/line/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["push_token_configured"] is False
    assert body["ready"] is False
    assert any("LINE_CHANNEL_ACCESS_TOKEN" in n for n in body["notes"])


# ── POST /admin/line/webhook ─────────────────────────────────────────────


class _FakeWebhookAdminClient:
    calls: ClassVar[list[dict[str, object]]] = []
    ensure_result: ClassVar[tuple[bool, bool]] = (True, False)
    test_result: ClassVar[dict[str, object]] = {"success": True, "statusCode": 200}
    ensure_error: ClassVar[WebhookApiError | None] = None

    def __init__(self, channel_access_token: str) -> None:
        self.channel_access_token = channel_access_token

    async def ensure_endpoint(self, url: str) -> tuple[bool, bool]:
        _FakeWebhookAdminClient.calls.append({"op": "ensure_endpoint", "url": url})
        if _FakeWebhookAdminClient.ensure_error is not None:
            raise _FakeWebhookAdminClient.ensure_error
        return _FakeWebhookAdminClient.ensure_result

    async def test_endpoint(self) -> dict[str, object]:
        _FakeWebhookAdminClient.calls.append({"op": "test_endpoint"})
        return _FakeWebhookAdminClient.test_result

    async def aclose(self) -> None:
        pass


@pytest_asyncio.fixture(autouse=True)
def _reset_fake_webhook_client():
    _FakeWebhookAdminClient.calls = []
    _FakeWebhookAdminClient.ensure_result = (True, False)
    _FakeWebhookAdminClient.test_result = {"success": True, "statusCode": 200}
    _FakeWebhookAdminClient.ensure_error = None
    yield


async def test_webhook_setup_requires_admin() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/admin/line/webhook", json={})
    assert resp.status_code in (401, 403)


async def test_webhook_setup_rejects_missing_token(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup, "get_settings", lambda: SimpleNamespace(line_channel_access_token="")
    )
    resp = await client.post("/admin/line/webhook", json={})
    assert resp.status_code == 422
    assert "LINE_CHANNEL_ACCESS_TOKEN" in resp.json()["error"]["message"]


async def test_webhook_setup_success_builds_url_and_runs_test(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(line_channel_access_token="real-token"),
    )
    monkeypatch.setattr(line_setup, "WebhookAdminClient", _FakeWebhookAdminClient)

    resp = await client.post(
        "/admin/line/webhook",
        json={"base_url": "https://chouhutiger.onrender.com"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["endpoint_url"] == "https://chouhutiger.onrender.com/line/webhook"
    assert body["changed"] is True
    assert body["active"] is False
    assert body["test_result"] == {"success": True, "statusCode": 200}
    assert _FakeWebhookAdminClient.calls == [
        {"op": "ensure_endpoint", "url": "https://chouhutiger.onrender.com/line/webhook"},
        {"op": "test_endpoint"},
    ]


async def test_webhook_setup_skips_test_when_requested(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(line_channel_access_token="real-token"),
    )
    monkeypatch.setattr(line_setup, "WebhookAdminClient", _FakeWebhookAdminClient)

    resp = await client.post(
        "/admin/line/webhook",
        json={"base_url": "https://chouhutiger.onrender.com", "run_test": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["test_result"] is None
    assert _FakeWebhookAdminClient.calls == [
        {"op": "ensure_endpoint", "url": "https://chouhutiger.onrender.com/line/webhook"},
    ]


async def test_webhook_setup_wraps_upstream_error_as_502(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(line_channel_access_token="real-token"),
    )
    _FakeWebhookAdminClient.ensure_error = WebhookApiError(400, "bad endpoint", "/endpoint")
    monkeypatch.setattr(line_setup, "WebhookAdminClient", _FakeWebhookAdminClient)

    resp = await client.post("/admin/line/webhook", json={})
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["code"] == "UPSTREAM_ERROR"
    assert body["error"]["details"]["status"] == 400


# ── POST /admin/line/finalize ────────────────────────────────────────────
#
# finalize_setup calls setup_liff / setup_richmenu / setup_webhook / line_status
# as plain function calls (not over HTTP), so it reads the same module-level
# LiffAdminClient / RichMenuAdminClient / WebhookAdminClient names — one fake
# per resource has to serve both the "do the ensure" call and the "read it
# back for status" call the way the real client would.


class _FakeFinalizeLiffClient:
    ensure_error: ClassVar[LiffApiError | None] = None
    registered: ClassVar[dict[str, str] | None] = None  # {"liffId": ..., "view_url": ...}

    def __init__(self, channel_access_token: str) -> None:
        pass

    async def ensure_app(self, *, view_url: str, description: str = "") -> tuple[str, bool]:
        if _FakeFinalizeLiffClient.ensure_error is not None:
            raise _FakeFinalizeLiffClient.ensure_error
        _FakeFinalizeLiffClient.registered = {"liffId": "FIN-LIFF-ID", "view_url": view_url}
        return "FIN-LIFF-ID", True

    async def list_apps(self) -> list[dict[str, object]]:
        if _FakeFinalizeLiffClient.registered is None:
            return []
        r = _FakeFinalizeLiffClient.registered
        return [{"liffId": r["liffId"], "view": {"url": r["view_url"]}}]

    async def aclose(self) -> None:
        pass


class _FakeFinalizeRichMenuClient:
    ensure_error: ClassVar[RichMenuApiError | None] = None
    default_id: ClassVar[str | None] = None

    def __init__(self, channel_access_token: str) -> None:
        pass

    async def ensure_richmenu(
        self, body: dict[str, object], image_bytes: bytes
    ) -> tuple[str, bool]:
        if _FakeFinalizeRichMenuClient.ensure_error is not None:
            raise _FakeFinalizeRichMenuClient.ensure_error
        _FakeFinalizeRichMenuClient.default_id = "FIN-RM-ID"
        return "FIN-RM-ID", False

    async def get_default_richmenu_id(self) -> str | None:
        return _FakeFinalizeRichMenuClient.default_id

    async def aclose(self) -> None:
        pass


class _FakeFinalizeWebhookClient:
    ensure_error: ClassVar[WebhookApiError | None] = None
    endpoint: ClassVar[str | None] = None
    active: ClassVar[bool] = False

    def __init__(self, channel_access_token: str) -> None:
        pass

    async def ensure_endpoint(self, url: str) -> tuple[bool, bool]:
        if _FakeFinalizeWebhookClient.ensure_error is not None:
            raise _FakeFinalizeWebhookClient.ensure_error
        _FakeFinalizeWebhookClient.endpoint = url
        return True, _FakeFinalizeWebhookClient.active

    async def test_endpoint(self) -> dict[str, object]:
        return {"success": True, "statusCode": 200}

    async def get_endpoint(self) -> tuple[str | None, bool]:
        return _FakeFinalizeWebhookClient.endpoint, _FakeFinalizeWebhookClient.active

    async def aclose(self) -> None:
        pass


@pytest_asyncio.fixture(autouse=True)
def _reset_fake_finalize_clients():
    _FakeFinalizeLiffClient.ensure_error = None
    _FakeFinalizeLiffClient.registered = None
    _FakeFinalizeRichMenuClient.ensure_error = None
    _FakeFinalizeRichMenuClient.default_id = None
    _FakeFinalizeWebhookClient.ensure_error = None
    _FakeFinalizeWebhookClient.endpoint = None
    _FakeFinalizeWebhookClient.active = False
    yield


def _patch_finalize_clients(monkeypatch) -> None:
    monkeypatch.setattr(line_setup, "LiffAdminClient", _FakeFinalizeLiffClient)
    monkeypatch.setattr(line_setup, "RichMenuAdminClient", _FakeFinalizeRichMenuClient)
    monkeypatch.setattr(line_setup, "WebhookAdminClient", _FakeFinalizeWebhookClient)


async def test_finalize_requires_admin() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/admin/line/finalize", json={})
    assert resp.status_code in (401, 403)


async def test_finalize_runs_all_three_and_reports_status(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(
            line_channel_access_token="real-token",
            line_channel_secret="sec",
            line_oa_add_friend_url="",
        ),
    )
    _patch_finalize_clients(monkeypatch)

    resp = await client.post(
        "/admin/line/finalize",
        json={"slug": "grand-open", "base_url": "https://chouhutiger.onrender.com"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["errors"] == []
    assert body["liff"]["liff_id"] == "FIN-LIFF-ID"
    assert body["richmenu"]["richmenu_id"] == "FIN-RM-ID"
    assert body["webhook"]["endpoint_url"] == "https://chouhutiger.onrender.com/line/webhook"
    # webhook toggle still off (no public API for it) -> not fully ready yet,
    # but everything this endpoint controls succeeded.
    assert body["status"]["liff_app_registered"] is True
    assert body["status"]["richmenu_set_as_default"] is True
    assert body["status"]["webhook_endpoint_matches"] is True
    assert body["status"]["webhook_active"] is False
    assert body["status"]["ready"] is False


async def test_finalize_collects_errors_without_aborting_remaining_steps(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_setup,
        "get_settings",
        lambda: SimpleNamespace(
            line_channel_access_token="real-token",
            line_channel_secret="sec",
            line_oa_add_friend_url="",
        ),
    )
    _patch_finalize_clients(monkeypatch)
    _FakeFinalizeLiffClient.ensure_error = LiffApiError(400, "bad view url", "/apps")

    resp = await client.post("/admin/line/finalize", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["liff"] is None
    assert len(body["errors"]) == 1
    assert "LIFF" in body["errors"][0]
    # richmenu and webhook still ran despite the LIFF failure.
    assert body["richmenu"]["richmenu_id"] == "FIN-RM-ID"
    assert body["webhook"]["endpoint_url"] is not None
