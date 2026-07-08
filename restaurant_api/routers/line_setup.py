"""``/admin/line`` — one-time LINE setup actions (admin-gated).

Currently just LIFF app registration: normally a manual click-through in
LINE Developers Console, but the channel access token already configured
for push (``LINE_CHANNEL_ACCESS_TOKEN``) also authorizes LINE's LIFF
management API, so the deploy that already has the token can register its
own LIFF app without an operator ever opening that console.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..api.auth import Admin
from ..api.errors import UpstreamError, ValidationError
from ..config import get_settings
from ..integrations.line import LiffAdminClient, LiffApiError

router = APIRouter(prefix="/admin/line", tags=["admin-line-setup"])


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


__all__ = ["router"]
