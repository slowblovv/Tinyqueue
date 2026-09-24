"""Atomic job-state transitions used by workers.

The core of the whole project lives here: claiming a job without two workers
ever grabbing the same row, and recovering jobs whose worker died mid-flight.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus
from app.queue.retry import compute_backoff_seconds, should_retry


async def reclaim_abandoned_jobs(db: AsyncSession, heartbeat_timeout: int) -> int:
    """Reset PROCESSING jobs whose worker heartbeat has gone stale back to PENDING.

    This is what prevents a job from being stuck forever if its worker crashes
    mid-execution (Challenge #2 in the spec).
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=heartbeat_timeout)
    stmt = (
        update(Job)
        .where(Job.status == JobStatus.PROCESSING, Job.heartbeat_at < cutoff)
        .values(
            status=JobStatus.PENDING,
            worker_id=None,
            locked_at=None,
            heartbeat_at=None,
            last_error="worker heartbeat timeout — job reclaimed",
        )
    )
    result = await db.execute(stmt)
    await db.commit()
    assert isinstance(result, CursorResult)
    return result.rowcount or 0


async def claim_job(db: AsyncSession, worker_id: str) -> Job | None:
    """Atomically select and lock the next claimable job.

    Uses `SELECT ... FOR UPDATE SKIP LOCKED` inside a single transaction so
    that concurrent workers never select the same row (Challenge #1): a
    worker that hits a locked row simply skips it instead of blocking or
    double-claiming.

    Ordering: priority DESC, available_at ASC, created_at ASC.
    """
    now = datetime.now(UTC)
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.PENDING, Job.available_at <= now)
        .order_by(Job.priority.desc(), Job.available_at.asc(), Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = await db.scalar(stmt)
    if job is None:
        await db.commit()  # release the (empty) transaction
        return None

    job.status = JobStatus.PROCESSING
    job.attempts += 1
    job.started_at = job.started_at or now
    job.worker_id = worker_id
    job.locked_at = now
    job.heartbeat_at = now
    job.version += 1

    await db.commit()
    await db.refresh(job)
    return job


async def heartbeat_job(db: AsyncSession, job_id, worker_id: str) -> None:
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.worker_id == worker_id, Job.status == JobStatus.PROCESSING)
        .values(heartbeat_at=datetime.now(UTC))
    )
    await db.execute(stmt)
    await db.commit()


async def complete_job(db: AsyncSession, job: Job) -> None:
    job.status = JobStatus.COMPLETED
    job.completed_at = datetime.now(UTC)
    job.last_error = None
    await db.commit()


async def fail_job(
    db: AsyncSession,
    job: Job,
    error: str,
    base_delay: float,
    jitter: float,
) -> None:
    """Handle a failed execution: retry with backoff, or move to FAILED."""
    job.last_error = error[:4000]

    if should_retry(job.attempts, job.max_attempts):
        delay = compute_backoff_seconds(job.attempts, base_delay, jitter)
        job.status = JobStatus.PENDING
        job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        job.worker_id = None
        job.locked_at = None
        job.heartbeat_at = None
    else:
        job.status = JobStatus.FAILED
        job.failed_at = datetime.now(UTC)

    await db.commit()
