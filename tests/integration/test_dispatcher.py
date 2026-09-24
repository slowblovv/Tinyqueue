import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Job, JobStatus
from app.queue.dispatcher import claim_job, complete_job, fail_job, reclaim_abandoned_jobs

pytestmark = pytest.mark.integration


async def _make_job(db_session, **overrides):
    defaults = dict(type="echo", payload={}, priority=0, max_attempts=3)
    defaults.update(overrides)
    job = Job(**defaults)
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


async def test_claim_marks_job_processing(db_session):
    job = await _make_job(db_session)
    claimed = await claim_job(db_session, worker_id="worker-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.PROCESSING
    assert claimed.attempts == 1
    assert claimed.worker_id == "worker-1"


async def test_claim_respects_priority_order(db_session):
    await _make_job(db_session, priority=1)
    high = await _make_job(db_session, priority=10)
    claimed = await claim_job(db_session, worker_id="worker-1")
    assert claimed.id == high.id


async def test_claim_skips_future_delayed_jobs(db_session):
    await _make_job(db_session, available_at=datetime.now(UTC) + timedelta(hours=1))
    claimed = await claim_job(db_session, worker_id="worker-1")
    assert claimed is None


async def test_two_concurrent_claims_never_get_same_job(engine):
    """The core correctness property: no two workers claim the same row."""
    from app.db import database as db_module

    async with db_module.session_scope() as setup_session:
        job = await _make_job(setup_session)

    async def claim_with_own_session(worker_id: str):
        async with db_module.session_scope() as session:
            return await claim_job(session, worker_id)

    results = await asyncio.gather(
        claim_with_own_session("worker-a"),
        claim_with_own_session("worker-b"),
    )
    non_none = [r for r in results if r is not None]
    assert len(non_none) == 1
    assert non_none[0].id == job.id


async def test_fail_job_retries_when_attempts_remain(db_session):
    job = await _make_job(db_session, max_attempts=3)
    job.status = JobStatus.PROCESSING
    job.attempts = 1
    await db_session.commit()

    await fail_job(db_session, job, "boom", base_delay=1.0, jitter=0.0)
    assert job.status == JobStatus.PENDING
    assert job.last_error == "boom"


async def test_fail_job_moves_to_failed_after_max_attempts(db_session):
    job = await _make_job(db_session, max_attempts=2)
    job.status = JobStatus.PROCESSING
    job.attempts = 2
    await db_session.commit()

    await fail_job(db_session, job, "boom", base_delay=1.0, jitter=0.0)
    assert job.status == JobStatus.FAILED
    assert job.failed_at is not None


async def test_complete_job_sets_completed_status(db_session):
    job = await _make_job(db_session)
    job.status = JobStatus.PROCESSING
    await db_session.commit()

    await complete_job(db_session, job)
    assert job.status == JobStatus.COMPLETED
    assert job.completed_at is not None


async def test_reclaim_abandoned_jobs_resets_stale_heartbeat(db_session):
    stale = datetime.now(UTC) - timedelta(seconds=120)
    job = await _make_job(
        db_session,
        status=JobStatus.PROCESSING,
        worker_id="dead-worker",
        heartbeat_at=stale,
    )

    reclaimed_count = await reclaim_abandoned_jobs(db_session, heartbeat_timeout=30)
    await db_session.refresh(job)

    assert reclaimed_count == 1
    assert job.status == JobStatus.PENDING
    assert job.worker_id is None


async def test_reclaim_leaves_fresh_heartbeats_alone(db_session):
    job = await _make_job(
        db_session,
        status=JobStatus.PROCESSING,
        worker_id="worker-1",
        heartbeat_at=datetime.now(UTC),
    )

    reclaimed_count = await reclaim_abandoned_jobs(db_session, heartbeat_timeout=30)
    await db_session.refresh(job)

    assert reclaimed_count == 0
    assert job.status == JobStatus.PROCESSING
