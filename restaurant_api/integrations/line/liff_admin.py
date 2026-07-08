"""LINE LIFF app management (api.line.me/liff/v1/apps) — a one-time setup call,
distinct from the ongoing message-push surface in ``messenger.py``.

The channel access token already configured for push (``LINE_CHANNEL_ACCESS_TOKEN``)
also authorizes this API, so the deploy that already has the token can register
its own LIFF app without an operator ever opening LINE Developers Console.

Best-effort by design: LINE's LIFF API has shifted shape across versions, so
if the real API disagrees with what's coded here, it fails loudly with the
response body intact rather than silently mis-registering — fall back to the
manual console steps in ``docs/16_line_oa_design.md`` §0-A if it does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import httpx

logger = logging.getLogger("restaurant_api.integrations.line.liff_admin")

LiffViewType = Literal["full", "tall", "compact"]


class LiffApiError(RuntimeError):
    """Non-2xx from the LIFF management API — carries status + body for the
    caller to surface verbatim (this is a manually-triggered admin action,
    not a background job, so a detailed error is more useful than a generic one)."""

    def __init__(self, status: int, body: str, path: str) -> None:
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f"LINE LIFF API {path} failed: HTTP {status}: {body[:500]}")


@dataclass
class LiffAdminClient:
    channel_access_token: str
    base_url: str = "https://api.line.me/liff/v1"
    timeout_seconds: float = 10.0
    transport: httpx.BaseTransport | None = None
    _client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                headers={"Authorization": f"Bearer {self.channel_access_token}"},
                transport=self.transport,  # type: ignore[arg-type]
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str) -> httpx.Response:
        """GET wrapper that turns a connection-level failure (DNS, proxy
        block, timeout — anything short of getting an HTTP response at all)
        into the same LiffApiError shape as a non-2xx response, so callers
        only ever need one except clause."""
        try:
            return await self._get_client().get(path)
        except httpx.HTTPError as e:
            raise LiffApiError(0, f"{type(e).__name__}: {e}", path) from e

    async def _post_json(self, path: str, body: dict[str, object]) -> httpx.Response:
        try:
            return await self._get_client().post(path, json=body)
        except httpx.HTTPError as e:
            raise LiffApiError(0, f"{type(e).__name__}: {e}", path) from e

    async def list_apps(self) -> list[dict[str, object]]:
        resp = await self._get("/apps")
        if resp.status_code >= 400:
            raise LiffApiError(resp.status_code, resp.text, "/apps")
        return list(resp.json().get("apps", []))

    async def create_app(
        self,
        *,
        view_url: str,
        view_type: LiffViewType = "full",
        description: str = "",
    ) -> str:
        body = {
            "view": {"type": view_type, "url": view_url},
            "description": description,
            "features": {"ble": False, "qrCode": False},
        }
        resp = await self._post_json("/apps", body)
        if resp.status_code >= 400:
            raise LiffApiError(resp.status_code, resp.text, "/apps")
        liff_id = resp.json().get("liffId")
        if not liff_id:
            raise LiffApiError(resp.status_code, resp.text, "/apps")
        return str(liff_id)

    async def ensure_app(
        self,
        *,
        view_url: str,
        view_type: LiffViewType = "full",
        description: str = "",
    ) -> tuple[str, bool]:
        """Idempotent: reuse an existing app whose view.url already matches
        ``view_url`` (same endpoint we'd otherwise register), else create one.

        Returns ``(liff_id, created)``.
        """
        existing = await self.list_apps()
        for app in existing:
            view = app.get("view")
            if isinstance(view, dict) and view.get("url") == view_url:
                liff_id = str(app.get("liffId", ""))
                if liff_id:
                    logger.info(
                        "liff_admin.reused_existing",
                        extra={"liff_id": liff_id, "view_url": view_url},
                    )
                    return liff_id, False
        liff_id = await self.create_app(
            view_url=view_url, view_type=view_type, description=description
        )
        logger.info("liff_admin.created", extra={"liff_id": liff_id, "view_url": view_url})
        return liff_id, True


__all__ = ["LiffAdminClient", "LiffApiError", "LiffViewType"]
