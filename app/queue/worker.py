"""Background worker process.

Usage:

    python -m app.queue.worker --workers 4

Each worker task independently polls for the next claimable job (atomic
claim via `SELECT ... FOR UPDATE SKIP LOCKED`), executes it through the
handler registry, and reports completion/failure. A separate scheduler task
periodically reclaims jobs abandoned by dead workers.

Graceful shutdown (SIGTERM/SIGINT): stop claiming new jobs, let the job
currently in flight finish, then exit cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import time
import uuid

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger, log_event
from app.core.metrics import (
    active_workers,
    job_processing_seconds,
    jobs_completed_total,
    jobs_failed_total,
    jobs_retried_total,
)
from app.db.database import dispose_engine, session_scope
from app.db.models import Job
from app.queue.dispatcher import claim_job, complete_job, fail_job, heartbeat_job
from app.queue.executor import execute_handler
from app.queue.scheduler import run_scheduler_loop

logger = get_logger(__name__)


class Worker:
    def __init__(self, worker_id: str, settings: Settings, stop_event: asyncio.Event):
        self.worker_id = worker_id
        self.settings = settings
        self.stop_event = stop_event

    async def run(self) -> None:
        log_event(logger, "worker_started", worker_id=self.worker_id)
        try:
            while not self.stop_event.is_set():
                job = await self._claim_next()
                if job is None:
                    await self._idle_wait()
                    continue
                await self._execute(job)
        finally:
            log_event(logger, "worker_stopped", worker_id=self.worker_id)

    async def _claim_next(self):
        async with session_scope() as db:
            return await claim_job(db, self.worker_id)

    async def _idle_wait(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                self.stop_event.wait(), timeout=self.settings.job_poll_interval
            )

    async def _execute(self, job) -> None:
        log_event(
            logger,
            "job_claimed",
            job_id=str(job.id),
            worker_id=self.worker_id,
            job_type=job.type,
            attempt=job.attempts,
        )
        log_event(logger, "job_started", job_id=str(job.id), worker_id=self.worker_id)

        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job.id))
        start = time.monotonic()
        try:
            timeout = job.timeout_seconds or self.settings.job_timeout
            await asyncio.wait_for(execute_handler(job.type, job.payload), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - we deliberately catch everything here
            duration_ms = int((time.monotonic() - start) * 1000)
            error_message = f"{type(exc).__name__}: {exc}"
            async with session_scope() as db:
                fresh = await db.get(Job, job.id)
                assert fresh is not None, "claimed job vanished before completion recording"
                await fail_job(
                    db,
                    fresh,
                    error_message,
                    self.settings.retry_base_delay,
                    self.settings.retry_jitter,
                )
                will_retry = fresh.status.value == "PENDING"
            event = "job_retry" if will_retry else "job_failed"
            (jobs_retried_total if will_retry else jobs_failed_total).inc()
            job_processing_seconds.labels(job_type=job.type).observe(duration_ms / 1000)
            log_event(
                logger,
                event,
                level=30,
                job_id=str(job.id),
                worker_id=self.worker_id,
                duration_ms=duration_ms,
                error=error_message,
                attempt=job.attempts,
            )
        else:
            duration_ms = int((time.monotonic() - start) * 1000)
            async with session_scope() as db:
                fresh = await db.get(Job, job.id)
                assert fresh is not None, "claimed job vanished before completion recording"
                await complete_job(db, fresh)
            jobs_completed_total.inc()
            job_processing_seconds.labels(job_type=job.type).observe(duration_ms / 1000)
            log_event(
                logger,
                "job_completed",
                job_id=str(job.id),
                worker_id=self.worker_id,
                duration_ms=duration_ms,
            )
        finally:
            heartbeat_task.cancel()

    async def _heartbeat_loop(self, job_id) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.heartbeat_interval)
                async with session_scope() as db:
                    await heartbeat_job(db, job_id, self.worker_id)
        except asyncio.CancelledError:
            pass


async def run_worker_pool(num_workers: int) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        log_event(logger, "shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        # Signal handlers aren't available on some platforms (e.g. Windows).
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_shutdown)

    workers = [
        Worker(worker_id=f"worker-{uuid.uuid4().hex[:5]}", settings=settings, stop_event=stop_event)
        for _ in range(num_workers)
    ]

    active_workers.set(num_workers)
    tasks = [asyncio.create_task(w.run()) for w in workers]
    tasks.append(
        asyncio.create_task(
            run_scheduler_loop(settings.heartbeat_timeout, settings.job_poll_interval, stop_event)
        )
    )

    await asyncio.gather(*tasks)
    active_workers.set(0)
    await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="TinyQueue background worker pool")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of concurrent worker tasks (defaults to WORKER_COUNT env var)",
    )
    args = parser.parse_args()

    num_workers = args.workers or get_settings().worker_count
    asyncio.run(run_worker_pool(num_workers))


if __name__ == "__main__":
    main()
