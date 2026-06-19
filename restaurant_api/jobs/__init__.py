"""Background jobs — nightly maintenance the schema implicitly requires.

Three jobs run via APScheduler (in-process):

  expiry_warning      03:00 daily  — flag ingredients within N days of expiry
  points_expire       03:30 daily  — write reversing rows for expired points
  campaign_expiry     03:45 daily  — expire wheel-spin vouchers past their window
  cogs_variance_check 04:00 daily  — compare actual vs theoretical COGS by store
  membership_lifecycle 04:15 daily — tier recompute + birthday + dormant win-back

The scheduler is intentionally *in-process* (not Celery / arq) for Phase 1.
Single-instance restaurant scale doesn't need distributed workers. Phase 2
swaps to arq when multiple uvicorn replicas come online.

Run standalone:  ``python -m restaurant_api.jobs``
Or via Make:     ``make jobs``
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import get_settings
from ..middleware import configure_logging
from .campaign_expiry import run_campaign_voucher_expiry
from .cogs_variance import run_cogs_variance_check
from .expiry_warning import run_expiry_warning
from .membership_lifecycle import run_membership_lifecycle
from .points_expire import run_points_expire

logger = logging.getLogger("restaurant_api.jobs")


JobFn = Callable[[], Awaitable[object]]  # jobs may return counts; we ignore the value


def _register(scheduler: AsyncIOScheduler) -> None:
    """Wire every job onto the scheduler with its cron trigger."""
    settings = get_settings()
    tz = settings.default_timezone

    scheduler.add_job(
        _wrap("expiry_warning", run_expiry_warning),
        trigger=CronTrigger(hour=3, minute=0, timezone=tz),
        id="expiry_warning",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _wrap("points_expire", run_points_expire),
        trigger=CronTrigger(hour=3, minute=30, timezone=tz),
        id="points_expire",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _wrap("campaign_expiry", run_campaign_voucher_expiry),
        trigger=CronTrigger(hour=3, minute=45, timezone=tz),
        id="campaign_expiry",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _wrap("cogs_variance_check", run_cogs_variance_check),
        trigger=CronTrigger(hour=4, minute=0, timezone=tz),
        id="cogs_variance_check",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _wrap("membership_lifecycle", run_membership_lifecycle),
        trigger=CronTrigger(hour=4, minute=15, timezone=tz),
        id="membership_lifecycle",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )


def _wrap(name: str, fn: JobFn) -> JobFn:
    """Wrap a job so a failure logs cleanly without killing the scheduler."""

    async def runner() -> None:
        logger.info("job.start", extra={"job": name})
        try:
            await fn()
            logger.info("job.complete", extra={"job": name})
        except Exception:
            logger.exception("job.failed", extra={"job": name})

    return runner


async def run_scheduler() -> None:
    """Bring up the scheduler and block until SIGTERM/SIGINT."""
    configure_logging()
    scheduler = AsyncIOScheduler()
    _register(scheduler)
    scheduler.start()
    logger.info(
        "scheduler.ready",
        extra={"jobs": [j.id for j in scheduler.get_jobs()]},
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # add_signal_handler is not available on Windows.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    logger.info("scheduler.shutdown_signal")
    scheduler.shutdown(wait=True)


__all__ = [
    "run_campaign_voucher_expiry",
    "run_cogs_variance_check",
    "run_expiry_warning",
    "run_membership_lifecycle",
    "run_points_expire",
    "run_scheduler",
]
