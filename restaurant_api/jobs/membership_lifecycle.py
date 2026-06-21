"""Nightly 會員生命週期 sweep — the engine that pulls customers back in store.

Runs estate-wide once a night and fires three retention levers, then pushes the
resulting LINE messages:

  1. Tier recompute (rolling 6-month spend) → congrats push on upgrades.
  2. Birthday gifts for 當日壽星 → birthday push.
  3. Dormant win-back for sleepers → comeback push.

Ordering guarantee: all DB grants are written and **committed first**, then the
LINE pushes go out. So a member never receives a message for a grant that failed
to persist, and the DB transaction isn't held open across external I/O. Pushes
themselves are best-effort (a LINE outage never fails the sweep).

Welcome bonuses are granted inline at the acquisition moment (wheel spin), not
here — see ``campaigns_service.spin`` / ``membership_service.grant_welcome_bonus``.

Idempotent: every lever is guarded by a DB-level uniqueness backstop, so a second
run the same day/period is a no-op.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_sessionmaker
from ..integrations.line import LineMessage, LineMessenger, get_messenger
from ..models import CustomerTier
from ..services import membership_service

logger = logging.getLogger("restaurant_api.jobs.membership_lifecycle")

_TIER_LABEL: dict[CustomerTier, str] = {
    CustomerTier.REGULAR: "一般會員",
    CustomerTier.SILVER: "銀卡",
    CustomerTier.GOLD: "金卡",
    CustomerTier.PLATINUM: "白金卡",
}


class _Push(NamedTuple):
    """A pending LINE push, collected during the DB phase, sent post-commit."""

    line_user_id: str
    text: str


async def run_membership_lifecycle(
    session: AsyncSession | None = None,
    messenger: LineMessenger | None = None,
) -> dict[str, int]:
    """Run the nightly lifecycle sweep. Returns per-lever counts.

    DB grants are committed before any LINE push is sent (see module docstring).
    When an external ``session`` is supplied (tests / embedded callers) the caller
    owns the transaction, so we just flush the grants and send afterwards.
    """
    out = messenger or get_messenger()
    if session is not None:
        summary, pushes = await _run(session)
        await _flush_pushes(out, pushes)
        return summary

    SessionLocal = get_sessionmaker()
    async with SessionLocal() as own_session:
        summary, pushes = await _run(own_session)
        await own_session.commit()  # persist grants BEFORE messaging
    await _flush_pushes(out, pushes)
    return summary


async def _run(session: AsyncSession) -> tuple[dict[str, int], list[_Push]]:
    """Apply the three levers (DB only) and collect the messages to send."""
    pushes: list[_Push] = []

    upgrades = 0
    for change in await membership_service.recompute_tiers(session):
        if change.is_upgrade:
            upgrades += 1
            _queue(
                pushes,
                change.customer.line_user_id,
                f"🎉 恭喜您升等為 {_TIER_LABEL[change.new_tier]}!\n"
                f"從今天起每筆消費點數加倍累積 謝謝您的支持 🙌",
            )

    birthdays = await membership_service.grant_birthday_gifts(session)
    for customer in birthdays:
        _queue(
            pushes,
            customer.line_user_id,
            f"🎂 {customer.display_name} 生日快樂!\n"
            f"送您 {membership_service.BIRTHDAY_POINTS} 點生日禮 "
            f"壽星 7 天內到店再招待一份甜點 等您來慶生 🥳",
        )

    winbacks = await membership_service.grant_dormant_winback(session)
    for customer in winbacks:
        _queue(
            pushes,
            customer.line_user_id,
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
    return summary, pushes


def _queue(pushes: list[_Push], line_user_id: str | None, text: str) -> None:
    """Collect a push for a LINE-reachable member; skip non-connected ones."""
    if line_user_id:
        pushes.append(_Push(line_user_id, text))


async def _flush_pushes(messenger: LineMessenger, pushes: list[_Push]) -> None:
    """Send queued pushes after commit — best-effort, never fails the sweep."""
    for push in pushes:
        try:
            await messenger.push(
                push.line_user_id, LineMessage(kind="text", text=push.text)
            )
        except Exception:  # best-effort side channel
            logger.warning(
                "membership_lifecycle.push_failed",
                extra={"line_user_id": push.line_user_id},
            )


__all__ = ["run_membership_lifecycle"]
