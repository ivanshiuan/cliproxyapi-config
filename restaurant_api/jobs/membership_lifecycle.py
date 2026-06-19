"""Nightly 會員生命週期 sweep — the engine that pulls customers back in store.

Runs estate-wide once a night and fires three retention levers, then pushes the
resulting LINE messages (fire-and-forget; a LINE outage never rolls back the
point grants):

  1. Tier recompute (rolling 6-month spend) → congrats push on upgrades.
  2. Birthday gifts for 當日壽星 → birthday push.
  3. Dormant win-back for sleepers → comeback push.

Welcome bonuses are granted inline at the acquisition moment (wheel spin), not
here — see ``campaigns_service.spin`` / ``membership_service.grant_welcome_bonus``.

Idempotent: every lever is guarded by a ledger marker, so a second run the same
day/period is a no-op.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_sessionmaker
from ..integrations.line import LineMessage, LineMessenger, get_messenger
from ..models import Customer, CustomerTier
from ..services import membership_service

logger = logging.getLogger("restaurant_api.jobs.membership_lifecycle")

_TIER_LABEL: dict[CustomerTier, str] = {
    CustomerTier.REGULAR: "一般會員",
    CustomerTier.SILVER: "銀卡",
    CustomerTier.GOLD: "金卡",
    CustomerTier.PLATINUM: "白金卡",
}


async def run_membership_lifecycle(
    session: AsyncSession | None = None,
    messenger: LineMessenger | None = None,
) -> dict[str, int]:
    """Run the nightly lifecycle sweep. Returns per-lever counts."""
    if session is not None:
        return await _run(session, messenger or get_messenger())
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as own_session:
        summary = await _run(own_session, messenger or get_messenger())
        await own_session.commit()
        return summary


async def _run(session: AsyncSession, messenger: LineMessenger) -> dict[str, int]:
    upgrades = 0
    for change in await membership_service.recompute_tiers(session):
        if change.is_upgrade:
            upgrades += 1
            await _push(
                messenger,
                change.customer,
                f"🎉 恭喜您升等為 {_TIER_LABEL[change.new_tier]}!\n"
                f"從今天起每筆消費點數加倍累積 謝謝您的支持 🙌",
            )

    birthdays = await membership_service.grant_birthday_gifts(session)
    for customer in birthdays:
        await _push(
            messenger,
            customer,
            f"🎂 {customer.display_name} 生日快樂!\n"
            f"送您 {membership_service.BIRTHDAY_POINTS} 點生日禮 "
            f"壽星 7 天內到店再招待一份甜點 等您來慶生 🥳",
        )

    winbacks = await membership_service.grant_dormant_winback(session)
    for customer in winbacks:
        await _push(
            messenger,
            customer,
            f"好久不見 {customer.display_name}!\n"
            f"送您 {membership_service.DORMANT_COMEBACK_POINTS} 點 "
            f"回來吃一次就能折抵 期待您再光臨 🍜",
        )

    summary = {
        "tier_upgrades": upgrades,
        "birthday_gifts": len(birthdays),
        "winbacks": len(winbacks),
    }
    logger.info("membership_lifecycle.complete", extra=summary)
    return summary


async def _push(messenger: LineMessenger, customer: Customer, text: str) -> None:
    """Best-effort LINE push — never let a messaging failure fail the sweep."""
    if not customer.line_user_id:
        return
    try:
        await messenger.push(customer.line_user_id, LineMessage(kind="text", text=text))
    except Exception:  # best-effort side channel
        logger.warning(
            "membership_lifecycle.push_failed",
            extra={"customer_id": str(customer.id)},
        )


__all__ = ["run_membership_lifecycle"]
