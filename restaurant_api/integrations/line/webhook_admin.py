"""LINE Messaging API webhook endpoint management
(api.line.me/v2/bot/channel/webhook/*) — closes the one manual-typing gap
in webhook setup: the endpoint URL itself is now set by API instead of
copy-pasted into LINE Developers Console, which is where a stray space or
missing ``/line/webhook`` suffix used to silently break delivery.

LINE does not expose a public API for the "Use webhook" on/off toggle
itself (only the endpoint URL and a connectivity test) — that one flip
still has to happen in the console. ``get_endpoint()``'s ``active`` field
reports its current state so a diagnostic can say exactly what's left.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .liff_admin import LiffApiError

WebhookApiError = LiffApiError  # same error shape as liff_admin/richmenu_admin


@dataclass
class WebhookAdminClient:
    channel_access_token: str
    base_url: str = "https://api.line.me/v2/bot"
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

    async def _call(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            resp = await self._get_client().request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as e:
            raise WebhookApiError(0, f"{type(e).__name__}: {e}", path) from e
        if resp.status_code >= 400:
            raise WebhookApiError(resp.status_code, resp.text, path)
        return resp

    async def get_endpoint(self) -> tuple[str | None, bool]:
        """Returns ``(endpoint_url, active)``. LINE returns 404 when no
        endpoint has ever been configured — treated as ``(None, False)``."""
        try:
            resp = await self._call("GET", "/channel/webhook/endpoint")
        except WebhookApiError as e:
            if e.status == 404:
                return None, False
            raise
        data = resp.json()
        endpoint = data.get("endpoint")
        return (str(endpoint) if endpoint else None), bool(data.get("active"))

    async def set_endpoint(self, url: str) -> None:
        await self._call("PUT", "/channel/webhook/endpoint", json={"endpoint": url})

    async def test_endpoint(self, url: str | None = None) -> dict[str, object]:
        """Asks LINE to fire a synthetic request at the configured (or given)
        endpoint right now — the fastest real signal that the deployed
        service is reachable and returns 200, short of a live phone test."""
        body: dict[str, object] = {"endpoint": url} if url else {}
        resp = await self._call("POST", "/channel/webhook/test", json=body)
        return dict(resp.json())

    async def ensure_endpoint(self, url: str) -> tuple[bool, bool]:
        """Idempotent: only calls ``set_endpoint`` if the current value
        differs. Returns ``(changed, active)``."""
        current, active = await self.get_endpoint()
        if current == url:
            return False, active
        await self.set_endpoint(url)
        return True, active


__all__ = ["WebhookAdminClient", "WebhookApiError"]
