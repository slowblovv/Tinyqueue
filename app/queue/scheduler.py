"""Background loop that reclaims jobs abandoned by dead workers.

Runs once per worker process (not once per worker task) alongside the worker
pool, on its own poll interval, independent from job claiming.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.logging import get_logger, log_event
from app.db.database import session_scope
from app.queue.dispatcher import reclaim_abandoned_jobs

logger = get_logger(__name__)


async def run_scheduler_loop(
    heartbeat_timeout: int,
    poll_interval: float,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            async with session_scope() as db:
                reclaimed = await reclaim_abandoned_jobs(db, heartbeat_timeout)
            if reclaimed:
                log_event(logger, "jobs_reclaimed", count=reclaimed)
        except Exception:
            logger.exception("scheduler loop iteration failed")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
