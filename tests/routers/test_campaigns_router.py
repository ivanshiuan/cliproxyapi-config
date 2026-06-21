"""Integration + service tests for the ``/campaigns`` wheel-spin lottery.

Mix of:
- Service-level tests (deterministic draw via a seeded RNG, validity-window
  date math, quota, daily limits, redemption rules) calling
  ``campaigns_service`` directly against the savepoint session.
- HTTP-level smoke tests through the mounted router.

All DB writes ride the per-test SAVEPOINT from ``conftest`` and roll back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from random import Random
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.auth import AdminPrincipal, require_admin
from restaurant_api.api.deps import get_current_tenant_id, get_db, get_line_messenger
from restaurant_api.api.errors import ConflictError, NotFoundError
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.main import app
from restaurant_api.models import (
    CampaignStatus,
    CampaignVoucher,
    MenuItem,
    Store,
    Tenant,
    VoucherStatus,
)
from restaurant_api.schemas.campaigns import (
    CampaignCreate,
    PrizeCreate,
    SpinRequest,
    VoucherRedeemRequest,
)
from restaurant_api.services import campaigns_service

pytestmark = pytest.mark.asyncio

_TPE = ZoneInfo("Asia/Taipei")


def _today() -> date:
    return datetime.now(_TPE).date()


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def stub_messenger() -> StubLineMessenger:
    return StubLineMessenger()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    stub_messenger: StubLineMessenger,
) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> uuid.UUID:
        return seed_tenant.id

    def _override_messenger() -> StubLineMessenger:
        return stub_messenger

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    app.dependency_overrides[get_line_messenger] = _override_messenger
    app.dependency_overrides[require_admin] = lambda: AdminPrincipal()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _slug() -> str:
    return f"open-{uuid.uuid4().hex[:8]}"


async def _make_campaign(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: CampaignStatus = CampaignStatus.ACTIVE,
    daily_spin_limit: int = 1,
    start_offset: int = 0,
    validity_days: int = 14,
    daily_message: str | None = None,
    store_id: uuid.UUID | None = None,
) -> uuid.UUID:
    resp = await campaigns_service.create_campaign(
        session,
        CampaignCreate(
            name="開幕輪盤",
            slug=_slug(),
            status=status,
            daily_spin_limit=daily_spin_limit,
            voucher_start_offset_days=start_offset,
            voucher_validity_days=validity_days,
            daily_message=daily_message,
            store_id=store_id,
        ),
        tenant_id=tenant_id,
    )
    return resp.id


async def _add_prize(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str = "和牛一盤",
    weight: int = 1,
    value: str = "500",
    menu_item_id: uuid.UUID | None = None,
    total_quota: int | None = None,
    daily_quota: int | None = None,
    sort_order: int = 0,
) -> uuid.UUID:
    resp = await campaigns_service.add_prize(
        session,
        campaign_id,
        PrizeCreate(
            name=name,
            weight=weight,
            value_estimate=Decimal(value),
            menu_item_id=menu_item_id,
            total_quota=total_quota,
            daily_quota=daily_quota,
            sort_order=sort_order,
        ),
        tenant_id=tenant_id,
    )
    return resp.id


def _spin_req(**over: Any) -> SpinRequest:
    base: dict[str, Any] = {"line_user_id": f"U{uuid.uuid4().hex[:16]}"}
    base.update(over)
    return SpinRequest(**base)


# ──────────────────────────────────────────────────────────────────────────
# Spin → register + mint voucher
# ──────────────────────────────────────────────────────────────────────────


async def test_spin_auto_registers_member_and_mints_voucher(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id)
    await _add_prize(
        db_session, cid, seed_tenant.id, value="500", menu_item_id=seed_menu_item.id
    )

    result = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
    )

    assert result.is_new_member is True
    assert result.won is True
    assert result.prize is not None
    assert result.voucher is not None
    assert result.voucher.status == VoucherStatus.ACTIVE
    assert result.spins_remaining_today == 0


async def test_validity_window_is_fixed_offset_plus_duration(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(
        db_session, seed_tenant.id, start_offset=3, validity_days=14
    )
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)

    result = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
    )
    assert result.voucher is not None
    expected_from = _today() + timedelta(days=3)
    assert result.voucher.valid_from == expected_from
    assert result.voucher.valid_until == expected_from + timedelta(days=14)


async def test_existing_member_not_recreated(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id, daily_spin_limit=2)
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)
    line_id = f"U{uuid.uuid4().hex[:16]}"

    first = await campaigns_service.spin(
        db_session, cid, _spin_req(line_user_id=line_id), tenant_id=seed_tenant.id, rng=Random(1)
    )
    second = await campaigns_service.spin(
        db_session, cid, _spin_req(line_user_id=line_id), tenant_id=seed_tenant.id, rng=Random(2)
    )
    assert first.is_new_member is True
    assert second.is_new_member is False
    assert first.customer_id == second.customer_id


# ──────────────────────────────────────────────────────────────────────────
# Daily spin limit
# ──────────────────────────────────────────────────────────────────────────


async def test_daily_spin_limit_blocks_second_spin(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id, daily_spin_limit=1)
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)
    line_id = f"U{uuid.uuid4().hex[:16]}"

    await campaigns_service.spin(
        db_session, cid, _spin_req(line_user_id=line_id), tenant_id=seed_tenant.id, rng=Random(1)
    )
    with pytest.raises(ConflictError):
        await campaigns_service.spin(
            db_session, cid, _spin_req(line_user_id=line_id), tenant_id=seed_tenant.id, rng=Random(1)
        )


async def test_spin_rejected_when_campaign_not_active(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id, status=CampaignStatus.DRAFT)
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)
    with pytest.raises(ConflictError):
        await campaigns_service.spin(
            db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
        )


# ──────────────────────────────────────────────────────────────────────────
# Weighted draw, quota, no-win
# ──────────────────────────────────────────────────────────────────────────


async def test_weighted_draw_favours_higher_weight(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id, daily_spin_limit=1)
    await _add_prize(
        db_session, cid, seed_tenant.id, name="銅板小菜", weight=90,
        value="50", menu_item_id=seed_menu_item.id, sort_order=0,
    )
    await _add_prize(
        db_session, cid, seed_tenant.id, name="免單四人", weight=1,
        value="2000", menu_item_id=seed_menu_item.id, sort_order=1,
    )

    rng = Random(42)
    counts: dict[str, int] = {"銅板小菜": 0, "免單四人": 0}
    for _ in range(200):
        result = await campaigns_service.spin(
            db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=rng
        )
        assert result.prize is not None
        counts[result.prize.name] += 1

    # Both reachable, common prize dominates (deterministic under fixed seed).
    assert counts["免單四人"] > 0
    assert counts["銅板小菜"] > counts["免單四人"]


async def test_total_quota_caps_grand_prize(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id)
    # Only one prize, capped at a single win for the whole campaign.
    await _add_prize(
        db_session, cid, seed_tenant.id, name="免單四人", weight=1,
        value="2000", menu_item_id=seed_menu_item.id, total_quota=1,
    )

    first = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
    )
    second = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(2)
    )
    assert first.won is True
    # Quota exhausted → no eligible prize → no-win spin, no voucher minted.
    assert second.won is False
    assert second.voucher is None


async def test_no_win_segment_records_spin_without_voucher(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id)
    # A "銘謝惠顧" segment: no menu item, zero value.
    await _add_prize(
        db_session, cid, seed_tenant.id, name="銘謝惠顧", value="0", menu_item_id=None
    )
    result = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
    )
    assert result.won is False
    assert result.voucher is None
    assert result.prize is None


# ──────────────────────────────────────────────────────────────────────────
# Daily pop-up message
# ──────────────────────────────────────────────────────────────────────────


async def test_daily_message_pushed_to_line(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_menu_item: MenuItem,
    stub_messenger: StubLineMessenger,
) -> None:
    cid = await _make_campaign(
        db_session, seed_tenant.id, daily_message="今日特餐 8 折,快來抽獎!"
    )
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)

    await campaigns_service.spin(
        db_session,
        cid,
        _spin_req(line_user_id="Uabc123"),
        tenant_id=seed_tenant.id,
        messenger=stub_messenger,
        rng=Random(1),
    )
    pushes = [m for m in stub_messenger.sent_messages if m["op"] == "push"]
    assert len(pushes) == 1
    assert pushes[0]["to"] == "Uabc123"


async def test_daily_message_push_failure_does_not_break_spin(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_menu_item: MenuItem,
) -> None:
    """A LINE push blowing up must NOT roll back the spin / lose the prize."""

    class _BoomMessenger(StubLineMessenger):
        async def push(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
            raise RuntimeError("LINE channel down")

    cid = await _make_campaign(db_session, seed_tenant.id, daily_message="抽獎囉")
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)

    res = await campaigns_service.spin(
        db_session,
        cid,
        _spin_req(line_user_id="Uboom"),
        tenant_id=seed_tenant.id,
        messenger=_BoomMessenger(),
        rng=Random(1),
    )
    # Spin succeeds and the voucher is minted despite the push raising.
    assert res.won is True
    assert res.voucher is not None


# ──────────────────────────────────────────────────────────────────────────
# Redemption
# ──────────────────────────────────────────────────────────────────────────


async def test_redeem_happy_path(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id)
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)
    spun = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
    )
    assert spun.voucher is not None

    redeemed = await campaigns_service.redeem_voucher(
        db_session, cid, spun.voucher.id, VoucherRedeemRequest(), tenant_id=seed_tenant.id
    )
    assert redeemed.status == VoucherStatus.REDEEMED
    assert redeemed.redeemed_on == _today()


async def test_one_redemption_per_visit_per_day(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    # Two spins in one day (limit 2) → two vouchers → only one redeemable today.
    cid = await _make_campaign(db_session, seed_tenant.id, daily_spin_limit=2)
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)
    line_id = f"U{uuid.uuid4().hex[:16]}"

    s1 = await campaigns_service.spin(
        db_session, cid, _spin_req(line_user_id=line_id), tenant_id=seed_tenant.id, rng=Random(1)
    )
    s2 = await campaigns_service.spin(
        db_session, cid, _spin_req(line_user_id=line_id), tenant_id=seed_tenant.id, rng=Random(2)
    )
    assert s1.voucher is not None and s2.voucher is not None

    await campaigns_service.redeem_voucher(
        db_session, cid, s1.voucher.id, VoucherRedeemRequest(), tenant_id=seed_tenant.id
    )
    with pytest.raises(ConflictError):
        await campaigns_service.redeem_voucher(
            db_session, cid, s2.voucher.id, VoucherRedeemRequest(), tenant_id=seed_tenant.id
        )


async def test_redeem_twice_same_voucher_conflicts(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id)
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)
    spun = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
    )
    assert spun.voucher is not None
    await campaigns_service.redeem_voucher(
        db_session, cid, spun.voucher.id, VoucherRedeemRequest(), tenant_id=seed_tenant.id
    )
    with pytest.raises(ConflictError):
        await campaigns_service.redeem_voucher(
            db_session, cid, spun.voucher.id, VoucherRedeemRequest(), tenant_id=seed_tenant.id
        )


async def test_redeem_expired_voucher_conflicts_and_marks_expired(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id)
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)
    spun = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
    )
    assert spun.voucher is not None

    # Backdate the validity window so it's already expired.
    row = (
        await db_session.execute(
            select(CampaignVoucher).where(CampaignVoucher.id == spun.voucher.id)
        )
    ).scalar_one()
    row.valid_from = _today() - timedelta(days=30)
    row.valid_until = _today() - timedelta(days=1)
    await db_session.flush()

    with pytest.raises(ConflictError):
        await campaigns_service.redeem_voucher(
            db_session, cid, spun.voucher.id, VoucherRedeemRequest(), tenant_id=seed_tenant.id
        )
    await db_session.refresh(row)
    assert row.status == VoucherStatus.EXPIRED


async def test_redeem_before_valid_from_conflicts(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id, start_offset=7)
    await _add_prize(db_session, cid, seed_tenant.id, menu_item_id=seed_menu_item.id)
    spun = await campaigns_service.spin(
        db_session, cid, _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
    )
    assert spun.voucher is not None
    assert spun.voucher.valid_from > _today()
    with pytest.raises(ConflictError):
        await campaigns_service.redeem_voucher(
            db_session, cid, spun.voucher.id, VoucherRedeemRequest(), tenant_id=seed_tenant.id
        )


async def test_wallet_lists_best_prize_first(
    db_session: AsyncSession, seed_tenant: Tenant, seed_menu_item: MenuItem
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id, daily_spin_limit=2)
    # total_quota=1 on each forces the two spins to win two *different* prizes,
    # so the ordering assertion below can't pass trivially on a tie.
    await _add_prize(
        db_session, cid, seed_tenant.id, name="小菜", weight=1, value="50",
        menu_item_id=seed_menu_item.id, sort_order=0, total_quota=1,
    )
    await _add_prize(
        db_session, cid, seed_tenant.id, name="大獎", weight=1, value="2000",
        menu_item_id=seed_menu_item.id, sort_order=1, total_quota=1,
    )
    line_id = f"U{uuid.uuid4().hex[:16]}"
    s1 = await campaigns_service.spin(
        db_session, cid, _spin_req(line_user_id=line_id), tenant_id=seed_tenant.id, rng=Random(1)
    )
    customer_id = s1.customer_id
    await campaigns_service.spin(
        db_session, cid, _spin_req(line_user_id=line_id), tenant_id=seed_tenant.id, rng=Random(7)
    )

    wallet = await campaigns_service.list_vouchers(
        db_session, cid, tenant_id=seed_tenant.id, customer_id=customer_id
    )
    assert len(wallet) == 2
    assert {v.value_estimate for v in wallet} == {Decimal("50"), Decimal("2000")}
    # Highest value first.
    assert wallet[0].value_estimate >= wallet[1].value_estimate


# ──────────────────────────────────────────────────────────────────────────
# HTTP smoke
# ──────────────────────────────────────────────────────────────────────────


async def test_create_campaign_add_prize_and_wheel_http(
    client: httpx.AsyncClient, seed_menu_item: MenuItem
) -> None:
    slug = _slug()
    resp = await client.post(
        "/campaigns",
        json={"name": "開幕輪盤", "slug": slug, "status": "active"},
    )
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]

    presp = await client.post(
        f"/campaigns/{cid}/prizes",
        json={
            "name": "免單四人",
            "weight": 1,
            "value_estimate": "2000",
            "menu_item_id": str(seed_menu_item.id),
            "total_quota": 5,
        },
    )
    assert presp.status_code == 201, presp.text

    wheel = await client.get(f"/campaigns/{cid}/wheel")
    assert wheel.status_code == 200
    assert wheel.json()["segments"][0]["name"] == "免單四人"


async def test_duplicate_slug_conflicts_http(client: httpx.AsyncClient) -> None:
    slug = _slug()
    body = {"name": "A", "slug": slug}
    assert (await client.post("/campaigns", json=body)).status_code == 201
    dup = await client.post("/campaigns", json={"name": "B", "slug": slug})
    assert dup.status_code == 409


async def test_spin_and_redeem_full_loop_http(
    client: httpx.AsyncClient, seed_menu_item: MenuItem
) -> None:
    create = await client.post(
        "/campaigns",
        json={"name": "開幕輪盤", "slug": _slug(), "status": "active"},
    )
    cid = create.json()["id"]
    await client.post(
        f"/campaigns/{cid}/prizes",
        json={
            "name": "和牛一盤",
            "weight": 1,
            "value_estimate": "500",
            "menu_item_id": str(seed_menu_item.id),
        },
    )

    spin = await client.post(
        f"/campaigns/{cid}/spin",
        json={"line_user_id": f"U{uuid.uuid4().hex[:16]}", "display_name": "新客"},
    )
    assert spin.status_code == 201, spin.text
    body = spin.json()
    assert body["won"] is True
    voucher_id = body["voucher"]["id"]
    customer_id = body["customer_id"]

    wallet = await client.get(
        f"/campaigns/{cid}/vouchers", params={"customer_id": customer_id}
    )
    assert wallet.status_code == 200
    assert len(wallet.json()) == 1

    redeem = await client.post(
        f"/campaigns/{cid}/vouchers/{voucher_id}/redeem", json={}
    )
    assert redeem.status_code == 200, redeem.text
    assert redeem.json()["status"] == "redeemed"


async def test_campaign_stats_dashboard_http(
    client: httpx.AsyncClient, seed_menu_item: MenuItem
) -> None:
    """Two players spin a single-prize (always-win) wheel; one redeems. The
    stats endpoint reports engagement, the voucher funnel, and prize spend.
    """
    cid = (
        await client.post(
            "/campaigns", json={"name": "成效測試", "slug": _slug(), "status": "active"}
        )
    ).json()["id"]
    await client.post(
        f"/campaigns/{cid}/prizes",
        json={
            "name": "和牛一盤",
            "weight": 1,
            "value_estimate": "500",
            "menu_item_id": str(seed_menu_item.id),
        },
    )

    voucher_ids = []
    for _ in range(2):  # two distinct members
        body = (
            await client.post(
                f"/campaigns/{cid}/spin",
                json={"line_user_id": f"U{uuid.uuid4().hex[:16]}", "display_name": "客"},
            )
        ).json()
        voucher_ids.append(body["voucher"]["id"])

    # One of the two redeems → funnel: 2 minted, 1 redeemed, 1 active.
    redeem = await client.post(f"/campaigns/{cid}/vouchers/{voucher_ids[0]}/redeem", json={})
    assert redeem.status_code == 200, redeem.text

    stats = await client.get(f"/campaigns/{cid}/stats")
    assert stats.status_code == 200, stats.text
    s = stats.json()

    assert s["total_spins"] == 2
    assert s["unique_players"] == 2
    assert s["winning_spins"] == 2
    assert s["win_rate"] == 1.0
    assert s["vouchers_minted"] == 2
    assert s["vouchers_redeemed"] == 1
    assert s["vouchers_active"] == 1
    assert s["redemption_rate"] == 0.5
    assert Decimal(str(s["value_redeemed"])) == Decimal("500")
    assert Decimal(str(s["value_outstanding"])) == Decimal("500")
    assert len(s["prizes"]) == 1
    assert s["prizes"][0]["awarded_count"] == 2
    assert s["prizes"][0]["remaining"] is None  # uncapped


async def test_campaign_stats_unknown_campaign_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/campaigns/{uuid.uuid4()}/stats")
    assert resp.status_code == 404


async def test_spin_requires_member_identity_http(client: httpx.AsyncClient) -> None:
    create = await client.post(
        "/campaigns", json={"name": "X", "slug": _slug(), "status": "active"}
    )
    cid = create.json()["id"]
    # No customer_id / line_user_id / phone → 422 from schema validation.
    resp = await client.post(f"/campaigns/{cid}/spin", json={})
    assert resp.status_code == 422


async def test_get_unknown_campaign_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/campaigns/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_spin_unknown_campaign_raises_not_found(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        await campaigns_service.spin(
            db_session, uuid.uuid4(), _spin_req(), tenant_id=seed_tenant.id, rng=Random(1)
        )


async def test_qr_svg_endpoint_returns_svg(client: httpx.AsyncClient) -> None:
    create = await client.post(
        "/campaigns", json={"name": "開幕輪盤", "slug": _slug(), "status": "active"}
    )
    cid = create.json()["id"]
    resp = await client.get(f"/campaigns/{cid}/qr.svg")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in resp.text and "<path" in resp.text


async def test_qr_unknown_campaign_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/campaigns/{uuid.uuid4()}/qr.svg")
    assert resp.status_code == 404


async def test_poster_endpoint_renders_brand(client: httpx.AsyncClient) -> None:
    create = await client.post(
        "/campaigns", json={"name": "開幕輪盤", "slug": _slug(), "status": "active"}
    )
    cid = create.json()["id"]
    resp = await client.get(
        f"/campaigns/{cid}/poster",
        params={"brand": "鼎鼎餐酒館", "tagline": "開幕同樂", "base_url": "https://shop.example"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "鼎鼎餐酒館" in body
    assert "開幕同樂" in body
    assert "<svg" in body  # QR embedded inline (encodes the branded demo URL)
    assert "掃我抽大獎" in body


async def test_store_scoped_campaign_roundtrips(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    cid = await _make_campaign(db_session, seed_tenant.id, store_id=seed_store.id)
    got = await campaigns_service.get_campaign(db_session, cid, tenant_id=seed_tenant.id)
    assert got.store_id == seed_store.id
