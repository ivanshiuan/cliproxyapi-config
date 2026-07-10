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
from ..api.errors import DomainError, UpstreamError, ValidationError
from ..config import get_settings
from ..integrations.line import (
    LiffAdminClient,
    LiffApiError,
    RichMenuAdminClient,
    RichMenuApiError,
    WebhookAdminClient,
    WebhookApiError,
)

router = APIRouter(prefix="/admin/line", tags=["admin-line-setup"])

_LINE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "line_assets"


class LineStatusResponse(BaseModel):
    """One-shot launch-readiness snapshot — everything a human (or Claude) needs
    to see whether the LINE side is fully wired, from a single call."""

    push_token_configured: bool
    webhook_secret_configured: bool
    liff_app_registered: bool
    liff_id: str | None
    liff_view_url: str | None
    richmenu_set_as_default: bool
    default_richmenu_id: str | None
    webhook_endpoint_url: str | None
    webhook_endpoint_matches: bool
    webhook_active: bool
    ready: bool
    notes: list[str]


@router.get(
    "/status",
    response_model=LineStatusResponse,
    summary="上線就緒度總覽 (token/secret/LIFF/圖文選單 一次看完)",
)
async def line_status(
    request: Request,
    _admin: Admin,
    slug: str = "grand-open",
    base_url: str | None = None,
) -> LineStatusResponse:
    settings = get_settings()
    token = settings.line_channel_access_token
    secret = settings.line_channel_secret
    notes: list[str] = []

    root = (base_url or str(request.base_url)).rstrip("/")
    expected_view_url = f"{root}/demo/campaign/{slug}"
    expected_webhook_url = f"{root}/line/webhook"

    liff_id: str | None = None
    liff_view_url: str | None = None
    default_richmenu_id: str | None = None
    webhook_endpoint_url: str | None = None
    webhook_active = False

    if not token:
        notes.append("LINE_CHANNEL_ACCESS_TOKEN 未設定 — LIFF/圖文選單/推播都無法運作")
    else:
        liff_client = LiffAdminClient(channel_access_token=token)
        try:
            for app in await liff_client.list_apps():
                view = app.get("view")
                if isinstance(view, dict) and view.get("url") == expected_view_url:
                    liff_id = str(app.get("liffId", "")) or None
                    liff_view_url = str(view.get("url"))
                    break
        except LiffApiError as e:
            notes.append(f"查 LIFF app 失敗: {e}")
        finally:
            await liff_client.aclose()

        rm_client = RichMenuAdminClient(channel_access_token=token)
        try:
            default_richmenu_id = await rm_client.get_default_richmenu_id()
        except RichMenuApiError as e:
            notes.append(f"查預設圖文選單失敗: {e}")
        finally:
            await rm_client.aclose()

        wh_client = WebhookAdminClient(channel_access_token=token)
        try:
            webhook_endpoint_url, webhook_active = await wh_client.get_endpoint()
        except WebhookApiError as e:
            notes.append(f"查 webhook endpoint 失敗: {e}")
        finally:
            await wh_client.aclose()

    webhook_endpoint_matches = webhook_endpoint_url == expected_webhook_url

    if not secret:
        notes.append("LINE_CHANNEL_SECRET 未設定 — 加好友歡迎訊息 webhook 無法驗證簽章")
    if liff_id is None and token:
        notes.append("尚未建立對應此活動的 LIFF app — 打 POST /admin/line/liff")
    if default_richmenu_id is None and token:
        notes.append("尚未設定預設圖文選單 — 打 POST /admin/line/richmenu")
    if token and not webhook_endpoint_matches:
        notes.append("webhook endpoint 尚未設成本服務網址 — 打 POST /admin/line/webhook")
    if token and webhook_endpoint_matches and not webhook_active:
        notes.append(
            "webhook endpoint 網址已對, 但「Use webhook」還是關的 —"
            " 這個開關 LINE 沒開放 API, 去 LINE Developers Console"
            " → Messaging API 分頁手動打開"
        )

    ready = bool(
        token and secret and liff_id and default_richmenu_id
        and webhook_endpoint_matches and webhook_active
    )
    if ready:
        notes.append("全部就緒: LIFF + 圖文選單 + webhook 端點與簽章金鑰都到位")

    return LineStatusResponse(
        push_token_configured=bool(token),
        webhook_secret_configured=bool(secret),
        liff_app_registered=liff_id is not None,
        liff_id=liff_id,
        liff_view_url=liff_view_url,
        richmenu_set_as_default=default_richmenu_id is not None,
        default_richmenu_id=default_richmenu_id,
        webhook_endpoint_url=webhook_endpoint_url,
        webhook_endpoint_matches=webhook_endpoint_matches,
        webhook_active=webhook_active,
        ready=ready,
        notes=notes,
    )


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


class WebhookSetupRequest(BaseModel):
    """All optional — matches this deployment's own ``/line/webhook`` route."""

    base_url: str | None = None
    run_test: bool = True


class WebhookSetupResponse(BaseModel):
    endpoint_url: str
    changed: bool
    active: bool
    test_result: dict[str, object] | None


@router.post(
    "/webhook",
    response_model=WebhookSetupResponse,
    summary="設定 webhook endpoint 網址, 免手動貼到 LINE Developers Console",
)
async def setup_webhook(
    payload: WebhookSetupRequest,
    request: Request,
    _admin: Admin,
) -> WebhookSetupResponse:
    """Sets the webhook endpoint URL via LINE's API — the one step that used
    to be copy-pasted by hand (and easy to typo). LINE still requires a
    console click to flip "Use webhook" on; that toggle has no public API.
    """
    settings = get_settings()
    token = settings.line_channel_access_token
    if not token:
        raise ValidationError(
            "LINE_CHANNEL_ACCESS_TOKEN 未設定 — 先在部署環境填入這把金鑰再呼叫此端點",
        )

    root = (payload.base_url or str(request.base_url)).rstrip("/")
    endpoint_url = f"{root}/line/webhook"

    client = WebhookAdminClient(channel_access_token=token)
    try:
        changed, active = await client.ensure_endpoint(endpoint_url)
        test_result: dict[str, object] | None = None
        if payload.run_test:
            test_result = await client.test_endpoint()
    except WebhookApiError as e:
        raise UpstreamError(
            f"LINE Webhook API 呼叫失敗: {e}",
            details={"status": e.status, "body": e.body[:1000], "path": e.path},
        ) from e
    finally:
        await client.aclose()

    return WebhookSetupResponse(
        endpoint_url=endpoint_url, changed=changed, active=active, test_result=test_result
    )


class FinalizeSetupRequest(BaseModel):
    """All optional — same defaults as the individual /liff, /richmenu,
    /webhook calls this chains together."""

    slug: str = "grand-open"
    base_url: str | None = None


class FinalizeSetupResponse(BaseModel):
    """Result of running all three one-time setup calls back to back, plus
    the resulting readiness snapshot — the single call the launch script
    needs instead of four separate round trips."""

    liff: LiffSetupResponse | None
    richmenu: RichMenuSetupResponse | None
    webhook: WebhookSetupResponse | None
    errors: list[str]
    status: LineStatusResponse


@router.post(
    "/finalize",
    response_model=FinalizeSetupResponse,
    summary="一次跑完 LIFF+圖文選單+webhook 三步, 最後回傳就緒度總覽",
)
async def finalize_setup(
    payload: FinalizeSetupRequest,
    request: Request,
    _admin: Admin,
) -> FinalizeSetupResponse:
    """Runs setup_liff, setup_richmenu, and setup_webhook in sequence — each
    is independently idempotent, so a partial failure here is safe to retry
    by just calling this again. Errors from one step don't block the others;
    they're collected so the final status snapshot still reflects whatever
    did succeed.
    """
    liff_result: LiffSetupResponse | None = None
    richmenu_result: RichMenuSetupResponse | None = None
    webhook_result: WebhookSetupResponse | None = None
    errors: list[str] = []

    liff_payload = LiffSetupRequest(slug=payload.slug, base_url=payload.base_url)
    try:
        liff_result = await setup_liff(liff_payload, request, _admin)
    except DomainError as e:
        detail = e.detail
        errors.append(f"LIFF: {detail.get('message') if isinstance(detail, dict) else detail}")

    richmenu_payload = RichMenuSetupRequest(slug=payload.slug, base_url=payload.base_url)
    try:
        richmenu_result = await setup_richmenu(richmenu_payload, request, _admin)
    except DomainError as e:
        detail = e.detail
        errors.append(
            f"圖文選單: {detail.get('message') if isinstance(detail, dict) else detail}"
        )

    webhook_payload = WebhookSetupRequest(base_url=payload.base_url)
    try:
        webhook_result = await setup_webhook(webhook_payload, request, _admin)
    except DomainError as e:
        detail = e.detail
        errors.append(f"Webhook: {detail.get('message') if isinstance(detail, dict) else detail}")

    status_result = await line_status(
        request, _admin, slug=payload.slug, base_url=payload.base_url
    )

    return FinalizeSetupResponse(
        liff=liff_result,
        richmenu=richmenu_result,
        webhook=webhook_result,
        errors=errors,
        status=status_result,
    )


__all__ = ["router"]
