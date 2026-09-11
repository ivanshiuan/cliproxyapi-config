"""Nightly Odoo AP sync — push received purchase orders as vendor bills.

Thin job wrapper over ``services.odoo_sync_service``. When Odoo is not
configured, ``get_odoo()`` returns the in-memory stub, so this job is a no-op
that logs what it *would* push -- safe to schedule before Odoo is live.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..services.odoo_sync_service import SyncReport, sync_purchase_orders

logger = logging.getLogger("restaurant_api.jobs.odoo_sync")


async def run_odoo_sync(session: AsyncSession | None = None) -> SyncReport:
    """Sync all tenants' received purchase orders into Odoo AP.

    ``session`` lets tests inject a rolled-back session; production creates its
    own via the service and commits.
    """
    report = await sync_purchase_orders(session=session)
    logger.info(
        "odoo_sync.job",
        extra={"pushed": report.pushed, "failed": report.failed},
    )
    return report


__all__ = ["run_odoo_sync"]
