"""FastAPI app entrypoint.

Production-ready surface:
  - structured-JSON logging with request_id correlation
  - K8s-grade /health/live + /health/ready split
  - graceful drain on shutdown (503 from /health/live while LB drains)
  - DomainError → ErrorResponse envelope for every 4xx/5xx
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import auth as auth_router
from .api import health as health_router
from .api.errors import DomainError, domain_error_handler
from .config import get_settings
from .database import dispose_engine
from .middleware import RequestContextMiddleware, configure_logging
from .routers import campaigns as campaigns_router
from .routers import cash_drawer as cash_drawer_router
from .routers import clock as clock_router
from .routers import customers as customers_router
from .routers import discount_rules as discount_rules_router
from .routers import employees as employees_router
from .routers import events as events_router
from .routers import kitchen as kitchen_router
from .routers import line_webhook as line_webhook_router
from .routers import membership as membership_router
from .routers import menu as menu_router
from .routers import online_takeout as online_takeout_router
from .routers import orders as orders_router
from .routers import pos_auth as pos_auth_router
from .routers import reports as reports_router
from .routers import reservations as reservations_router
from .routers import stock as stock_router
from .routers import stores as stores_router
from .routers import table_order as table_order_router
from .routers import tables as tables_router
from .routers import ugc as ugc_router
from .services.holiday_calendar import refresh_singleton as refresh_holiday_cache

logger = logging.getLogger("restaurant_api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    logger.info(
        "restaurant_api.starting",
        extra={"env": settings.env, "version": __version__},
    )
    # Prime the in-memory Taiwan holiday cache. Failure here is non-fatal —
    # the calendar falls back to weekend-only mode and the warning surfaces
    # in the /health/ready check downstream.
    try:
        n = await refresh_holiday_cache()
        logger.info("holiday_cache.primed", extra={"holiday_count": n})
    except Exception as e:
        logger.warning(
            "holiday_cache.prime_failed",
            extra={"error": str(e), "fallback": "weekend_only"},
        )
    yield
    health_router.mark_shutting_down()
    logger.info("restaurant_api.shutting_down")
    await dispose_engine()


app = FastAPI(
    title="Taiwan AI Restaurant SaaS — Backend",
    version=__version__,
    lifespan=lifespan,
)

# Request-context middleware FIRST so every downstream log line is tagged.
app.add_middleware(RequestContextMiddleware)

# Domain-error → JSON envelope handler (single shape for every 4xx/5xx).
app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]


# Mount routers. Each router module's tests also self-mount with a guard,
# so the import order doesn't matter — mounting here is the "real prod"
# wiring; the in-test self-mount becomes a no-op once we're already here.
def _mount_router(module, prefix_hint: str) -> None:
    """Idempotent include_router — skip if any route under prefix already exists."""
    if any(getattr(r, "path", "").startswith(prefix_hint) for r in app.routes):
        return
    app.include_router(module.router)


_mount_router(orders_router, "/orders")
_mount_router(menu_router, "/menu")
_mount_router(tables_router, "/tables")
_mount_router(reports_router, "/reports")
_mount_router(pos_auth_router, "/pos-auth")
_mount_router(cash_drawer_router, "/cash-drawer")
_mount_router(discount_rules_router, "/discount-rules")
_mount_router(employees_router, "/employees")
_mount_router(online_takeout_router, "/online-takeout")
_mount_router(table_order_router, "/t")
_mount_router(stock_router, "/stock")
_mount_router(stores_router, "/stores")
_mount_router(clock_router, "/clock")
_mount_router(events_router, "/events")
_mount_router(kitchen_router, "/kitchen")
_mount_router(customers_router, "/customers")
_mount_router(campaigns_router, "/campaigns")
_mount_router(ugc_router, "/ugc")
_mount_router(membership_router, "/membership")
_mount_router(line_webhook_router, "/line")
# Reservations module exports two routers (one per prefix); mount each with
# the path-based idempotency guard.
if not any(getattr(r, "path", "").startswith("/reservations") for r in app.routes):
    app.include_router(reservations_router.reservations_router)
if not any(getattr(r, "path", "").startswith("/queue") for r in app.routes):
    app.include_router(reservations_router.queue_router)
app.include_router(health_router.router)
if not any(getattr(r, "path", "").startswith("/admin") for r in app.routes):
    app.include_router(auth_router.router)

# Static wheel-spin demo page (門口 QR Code 指向 /demo/?campaign=<id>). Same-origin
# with the API so no CORS is needed. Mounted last so it can't shadow API routes.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/demo", StaticFiles(directory=str(_STATIC_DIR), html=True), name="demo")

# POS front-end (P1.6) — floor-plan board + menu browser, runs on any tablet
# browser. Same-origin with the API. Served at /pos/.
_POS_DIR = Path(__file__).parent / "pos_static"
if _POS_DIR.is_dir():
    app.mount("/pos", StaticFiles(directory=str(_POS_DIR), html=True), name="pos")

# Customer table-QR ordering page (P2) — the URL a table card's QR encodes is
# /order/?t=<qr_token>. Mobile-first, same-origin with the /t API.
_ORDER_DIR = Path(__file__).parent / "order_static"
if _ORDER_DIR.is_dir():
    app.mount("/order", StaticFiles(directory=str(_ORDER_DIR), html=True), name="order")

# KDS kitchen display (P3) — /kds/?store=<uuid> on the kitchen screen. Rides the
# same /tables/ws live stream as the POS floor board.
_KDS_DIR = Path(__file__).parent / "kds_static"
if _KDS_DIR.is_dir():
    app.mount("/kds", StaticFiles(directory=str(_KDS_DIR), html=True), name="kds")

# Consumer online-booking page (P6) — /book/?store=<uuid>, posts /reservations
# with source=online; staff seats the party from the POS reservation panel.
_BOOK_DIR = Path(__file__).parent / "book_static"
if _BOOK_DIR.is_dir():
    app.mount("/book", StaticFiles(directory=str(_BOOK_DIR), html=True), name="book")

# Consumer online-takeout page (P7.5) — /takeout/?store=<uuid>, posts
# /online-takeout; staff collect cash at pickup from the POS panel.
_TAKEOUT_DIR = Path(__file__).parent / "takeout_static"
if _TAKEOUT_DIR.is_dir():
    app.mount("/takeout", StaticFiles(directory=str(_TAKEOUT_DIR), html=True), name="takeout")

# Public 取餐叫號 display (P8.1) — /pickup-board/?store=<uuid>, rides the
# same /tables/ws floor stream; front-of-house TV points here.
_PICKUP_BOARD_DIR = Path(__file__).parent / "pickup_board_static"
if _PICKUP_BOARD_DIR.is_dir():
    app.mount(
        "/pickup-board", StaticFiles(directory=str(_PICKUP_BOARD_DIR), html=True), name="pickup-board"
    )

# 總部多店儀表板 (P8.3) — /hq/?date_from=&date_to=, ranks every active store
# under the tenant off GET /reports/hq. No store_id: this is the one screen
# that deliberately spans every store.
_HQ_DIR = Path(__file__).parent / "hq_static"
if _HQ_DIR.is_dir():
    app.mount("/hq", StaticFiles(directory=str(_HQ_DIR), html=True), name="hq")

# Kiosk 自助點餐機 (P9.1, docs/23 G1) — /kiosk/?store=<uuid> on an in-store
# tablet/kiosk. Orders land as channel TABLET on the same 外帶待取 flow.
_KIOSK_DIR = Path(__file__).parent / "kiosk_static"
if _KIOSK_DIR.is_dir():
    app.mount("/kiosk", StaticFiles(directory=str(_KIOSK_DIR), html=True), name="kiosk")

# 遠端線上候位 (P9.2, docs/23 G6) — /waitlist/?store=<uuid>: 顧客掃店門口 QR
# 自助取號、手機看排隊進度; 進度輪詢公開安全的 GET /queue/board。
_WAITLIST_DIR = Path(__file__).parent / "waitlist_static"
if _WAITLIST_DIR.is_dir():
    app.mount("/waitlist", StaticFiles(directory=str(_WAITLIST_DIR), html=True), name="waitlist")

# P10 重製版 POS UI (docs/25/26) — /pos-v2/ 依 iCHEF+Square/Toast 設計規範重做的
# 頂級介面: 四色桌況、雙欄點餐、數字鍵盤結帳(動態快捷現金+自動找零)、廚房出單、
# 比較式報表。目前為「設計驗證版」(前端狀態自帶示範資料); 下一步接真 REST API
# 取代舊 /pos。Ivan 認可設計方向後才接線, 避免白工。
_POS_V2_DIR = Path(__file__).parent / "pos_ui_v2"
if _POS_V2_DIR.is_dir():
    app.mount("/pos-v2", StaticFiles(directory=str(_POS_V2_DIR), html=True), name="pos-v2")

# P11 後台管理畫面 — /admin-ui/?store=<uuid>: 菜單分類/品項、桌位、自動折扣規則的
# 圖形化 CRUD (本來只有 REST API 沒有管理介面). 同一套 Apple HIG 設計語言, 接真
# /menu /tables /discount-rules /stores 端點. 這是「前台後台」完整拼圖的最後一塊.
_ADMIN_UI_DIR = Path(__file__).parent / "admin_ui"
if _ADMIN_UI_DIR.is_dir():
    app.mount("/admin-ui", StaticFiles(directory=str(_ADMIN_UI_DIR), html=True), name="admin-ui")


@app.get("/version", tags=["meta"])
async def version() -> dict[str, str]:
    """App version + build info."""
    return {"version": __version__, "name": get_settings().app_name}


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "service": "restaurant_api",
        "version": __version__,
        "docs": "/docs",
    }
