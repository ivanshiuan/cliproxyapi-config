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
from restaurant_api.integrations.line import LiffApiError, RichMenuApiError
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
