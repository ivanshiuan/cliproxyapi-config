"""``python -m restaurant_api.jobs`` runs the scheduler. Used by Make + Docker CMD."""

from __future__ import annotations

import asyncio

from . import run_scheduler

if __name__ == "__main__":
    asyncio.run(run_scheduler())
