"""``/admin/line`` — one-time LINE setup actions (admin-gated).

LIFF registration and rich-menu upload are normally manual click-throughs in
LINE Developers Console / Official Account Manager, but the channel access
token already configured for push (``LINE_CHANNEL_ACCESS_TOKEN``) also
authorizes both of those management APIs — so the deploy that already has
the token can do its own setup without an operator opening either console.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..api.auth import Admin
from ..api.errors import UpstreamError, ValidationError
from ..config import get_settings
from ..integrations.line import (
    LiffAdminClient,
    LiffApiError,
    RichMenuAdminClient,
    RichMenuApiError,
)

router = APIRouter(prefix="/admin/line", tags=["admin-line-setup"])

_LINE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "line_assets"


class LiffSetupRequest(BaseModel):
    """All optional — sensible defaults cover the single-campaign launch case."""

    slug: str = "grand-open"
    base_url: str | None = None
    description: str = ""


class LiffSetupResponse(BaseModel):
    liff_id: str
    created: bool
    view_url: str
    wheel_url_with_liff: str


@router.post(
    "/liff",
    response_model=LiffSetupResponse,
    summary="註冊(或重用既有) LIFF app, 免開 LINE Developers Console",
)
async def setup_liff(
    payload: LiffSetupRequest,
    request: Request,
    _admin: Admin,
) -> LiffSetupResponse:
    settings = get_settings()
    token = settings.line_channel_access_token
    if not token:
        raise ValidationError(
            "LINE_CHANNEL_ACCESS_TOKEN 未設定 — 先在部署環境填入這把金鑰再呼叫此端點",
        )

    root = (payload.base_url or str(request.base_url)).rstrip("/")
    view_url = f"{root}/demo/campaign/{payload.slug}"
    description = payload.description or f"開幕輪盤-{payload.slug}"

    client = LiffAdminClient(channel_access_token=token)
    try:
        liff_id, created = await client.ensure_app(
            view_url=view_url, description=description
        )
    except LiffApiError as e:
        raise UpstreamError(
            f"LINE LIFF API 呼叫失敗: {e}",
            details={"status": e.status, "body": e.body[:1000], "path": e.path},
        ) from e
    finally:
        await client.aclose()

    return LiffSetupResponse(
        liff_id=liff_id,
        created=created,
        view_url=view_url,
        wheel_url_with_liff=f"{view_url}?liff={liff_id}",
    )


class RichMenuSetupRequest(BaseModel):
    """All optional — defaults match the launch-ready 2-button menu already
    committed at restaurant_api/line_assets/richmenu_launch.png."""

    slug: str = "grand-open"
    base_url: str | None = None
    name: str = "周霸虎-開幕上線版-v1"
    chat_bar_text: str = "🎡 開幕輪盤抽獎"


class RichMenuSetupResponse(BaseModel):
    richmenu_id: str
    replaced_existing: bool
    wheel_url: str


@router.post(
    "/richmenu",
    response_model=RichMenuSetupResponse,
    summary="上傳圖文選單並設為預設, 免開 LINE Official Account Manager",
)
async def setup_richmenu(
    payload: RichMenuSetupRequest,
    request: Request,
    _admin: Admin,
) -> RichMenuSetupResponse:
    settings = get_settings()
    token = settings.line_channel_access_token
    if not token:
        raise ValidationError(
            "LINE_CHANNEL_ACCESS_TOKEN 未設定 — 先在部署環境填入這把金鑰再呼叫此端點",
        )

    image_path = _LINE_ASSETS_DIR / "richmenu_launch.png"
    if not image_path.is_file():
        raise ValidationError(
            f"找不到圖文選單圖檔: {image_path} — 先跑 scripts/render_richmenu_png.py 產生",
        )
    image_bytes = image_path.read_bytes()

    root = (payload.base_url or str(request.base_url)).rstrip("/")
    wheel_url = f"{root}/demo/campaign/{payload.slug}"
    body: dict[str, object] = {
        "size": {"width": 2500, "height": 843},
        "selected": True,
        "name": payload.name,
        "chatBarText": payload.chat_bar_text,
        "areas": [
            {
                "bounds": {"x": 0, "y": 0, "width": 1250, "height": 843},
                "action": {"type": "uri", "label": "開幕輪盤抽獎", "uri": wheel_url},
            },
            {
                "bounds": {"x": 1250, "y": 0, "width": 1250, "height": 843},
                "action": {"type": "uri", "label": "我的獎品錢包", "uri": wheel_url},
            },
        ],
    }

    client = RichMenuAdminClient(channel_access_token=token)
    try:
        richmenu_id, replaced = await client.ensure_richmenu(body, image_bytes)
    except RichMenuApiError as e:
        raise UpstreamError(
            f"LINE Rich Menu API 呼叫失敗: {e}",
            details={"status": e.status, "body": e.body[:1000], "path": e.path},
        ) from e
    finally:
        await client.aclose()

    return RichMenuSetupResponse(
        richmenu_id=richmenu_id, replaced_existing=replaced, wheel_url=wheel_url
    )


__all__ = ["router"]
