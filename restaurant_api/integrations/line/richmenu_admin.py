"""LINE Rich Menu management — a one-time (or re-run-on-change) setup call,
same shape as ``liff_admin.py``: the channel access token already configured
for push also authorizes this API, so a deploy that already has the token
can upload its own rich menu without an operator opening LINE Official
Account Manager.

Two hosts are involved — LINE splits menu-definition (JSON, ``api.line.me``)
from image content (binary, ``api-data.line.me``) across separate hosts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .liff_admin import LiffApiError  # same error shape; the host differs, not the contract

RichMenuApiError = LiffApiError  # re-exported alias: callers shouldn't care which LINE sub-API failed


@dataclass
class RichMenuAdminClient:
    channel_access_token: str
    api_base_url: str = "https://api.line.me/v2/bot"
    data_base_url: str = "https://api-data.line.me/v2/bot"
    timeout_seconds: float = 30.0  # image upload is slower than JSON calls
    transport: httpx.BaseTransport | None = None
    _api_client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)
    _data_client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)

    def _get_api_client(self) -> httpx.AsyncClient:
        if self._api_client is None:
            self._api_client = httpx.AsyncClient(
                base_url=self.api_base_url,
                timeout=self.timeout_seconds,
                headers={"Authorization": f"Bearer {self.channel_access_token}"},
                transport=self.transport,  # type: ignore[arg-type]
            )
        return self._api_client

    def _get_data_client(self) -> httpx.AsyncClient:
        if self._data_client is None:
            self._data_client = httpx.AsyncClient(
                base_url=self.data_base_url,
                timeout=self.timeout_seconds,
                headers={"Authorization": f"Bearer {self.channel_access_token}"},
                transport=self.transport,  # type: ignore[arg-type]
            )
        return self._data_client

    async def aclose(self) -> None:
        if self._api_client is not None:
            await self._api_client.aclose()
            self._api_client = None
        if self._data_client is not None:
            await self._data_client.aclose()
            self._data_client = None

    async def _call(
        self, client: httpx.AsyncClient, method: str, path: str, **kwargs: object
    ) -> httpx.Response:
        try:
            resp = await client.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as e:
            raise RichMenuApiError(0, f"{type(e).__name__}: {e}", path) from e
        if resp.status_code >= 400:
            raise RichMenuApiError(resp.status_code, resp.text, path)
        return resp

    async def list_richmenus(self) -> list[dict[str, object]]:
        resp = await self._call(self._get_api_client(), "GET", "/richmenu/list")
        return list(resp.json().get("richmenus", []))

    async def create_richmenu(self, body: dict[str, object]) -> str:
        resp = await self._call(self._get_api_client(), "POST", "/richmenu", json=body)
        richmenu_id = resp.json().get("richMenuId")
        if not richmenu_id:
            raise RichMenuApiError(resp.status_code, resp.text, "/richmenu")
        return str(richmenu_id)

    async def delete_richmenu(self, richmenu_id: str) -> None:
        await self._call(self._get_api_client(), "DELETE", f"/richmenu/{richmenu_id}")

    async def upload_image(
        self, richmenu_id: str, image_bytes: bytes, content_type: str = "image/png"
    ) -> None:
        await self._call(
            self._get_data_client(),
            "POST",
            f"/richmenu/{richmenu_id}/content",
            content=image_bytes,
            headers={"Content-Type": content_type},
        )

    async def set_default(self, richmenu_id: str) -> None:
        await self._call(self._get_api_client(), "POST", f"/user/all/richmenu/{richmenu_id}")

    async def get_default_richmenu_id(self) -> str | None:
        """The rich menu currently shown to all users, or None if none is set.
        LINE returns 404 when no default is configured — treated as None here."""
        try:
            resp = await self._call(self._get_api_client(), "GET", "/user/all/richmenu")
        except RichMenuApiError as e:
            if e.status == 404:
                return None
            raise
        rid = resp.json().get("richMenuId")
        return str(rid) if rid else None

    async def ensure_richmenu(
        self, body: dict[str, object], image_bytes: bytes, *, content_type: str = "image/png"
    ) -> tuple[str, bool]:
        """Idempotent-by-name: LINE's rich menu has no update-in-place API, so
        a matching ``name`` from a prior run is deleted and replaced fresh
        rather than left to accumulate duplicates on every re-run.

        Returns ``(richmenu_id, replaced_existing)``.
        """
        name = body.get("name")
        existing = await self.list_richmenus()
        replaced = False
        for menu in existing:
            if name is not None and menu.get("name") == name:
                old_id = str(menu.get("richMenuId", ""))
                if old_id:
                    await self.delete_richmenu(old_id)
                    replaced = True

        richmenu_id = await self.create_richmenu(body)
        await self.upload_image(richmenu_id, image_bytes, content_type=content_type)
        await self.set_default(richmenu_id)
        return richmenu_id, replaced


__all__ = ["RichMenuAdminClient", "RichMenuApiError"]
