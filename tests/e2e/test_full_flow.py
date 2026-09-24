"""End-to-end tests: bring up the API and real worker logic against PostgreSQL.

Requires a real database (see tests/integration/conftest.py for TEST_DATABASE_URL).
These tests reuse the `engine`/`client`/`db_session` fixtures from tests/integration
by importing its conftest via pytest's rootdir discovery — run with:

    pytest tests/e2e
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Job
from app.queue.dispatcher import claim_job, complete_job, fail_job, reclaim_abandoned_jobs
from app.queue.executor import execute_handler

pytestmark = pytest.mark.integration


async def _run_one_job_to_completion(db_session, worker_id="e2e-worker"):
    job = await claim_job(db_session, worker_id)
    if job is None:
        return None
    try:
        result = await execute_handler(job.type, job.payload)
    except Exception as exc:
        await fail_job(db_session, job, str(exc), base_delay=0.01, jitter=0)
    else:
        await complete_job(db_session, job)
        return result
    return None


async def test_post_job_flows_through_worker_to_completed(client, db_session):
    resp = await client.post("/api/v1/jobs", json={"type": "echo", "payload": {"message": "e2e"}})
    job_id = resp.json()["id"]

    result = await _run_one_job_to_completion(db_session)
    assert result == {"echo": "e2e"}

    check = await client.get(f"/api/v1/jobs/{job_id}")
    assert check.json()["status"] == "COMPLETED"


async def test_failing_job_retries_then_reaches_failed(client, db_session):
    resp = await client.post(
        "/api/v1/jobs",
        json={"type": "fail", "payload": {"error": "boom"}, "max_attempts": 2},
    )
    job_id = resp.json()["id"]

    # First attempt fails -> retried (available_at pushed into the future).
    await _run_one_job_to_completion(db_session)
    first = await client.get(f"/api/v1/jobs/{job_id}")
    assert first.json()["status"] == "PENDING"
    assert first.json()["attempts"] == 1

    # Force availability so we don't have to sleep out the backoff in tests.
    job = await db_session.get(Job, job_id)
    job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    # Second attempt fails -> max_attempts reached -> FAILED.
    await _run_one_job_to_completion(db_session)
    second = await client.get(f"/api/v1/jobs/{job_id}")
    assert second.json()["status"] == "FAILED"
    assert second.json()["attempts"] == 2


async def test_worker_crash_recovery_full_cycle(client, db_session):
    """create -> claim -> (simulate crash: no heartbeat) -> reclaimed -> new worker completes."""
    resp = await client.post("/api/v1/jobs", json={"type": "echo", "payload": {"message": "x"}})
    job_id = resp.json()["id"]

    claimed = await claim_job(db_session, worker_id="worker-doomed")
    assert claimed is not None

    # Simulate the worker dying: heartbeat goes stale.
    claimed.heartbeat_at = datetime.now(UTC) - timedelta(seconds=120)
    await db_session.commit()

    reclaimed = await reclaim_abandoned_jobs(db_session, heartbeat_timeout=30)
    assert reclaimed == 1

    mid = await client.get(f"/api/v1/jobs/{job_id}")
    assert mid.json()["status"] == "PENDING"

    result = await _run_one_job_to_completion(db_session, worker_id="worker-new")
    assert result == {"echo": "x"}

    final = await client.get(f"/api/v1/jobs/{job_id}")
    assert final.json()["status"] == "COMPLETED"


async def test_no_two_healthy_workers_claim_same_job_concurrently(engine):
    """Concurrency property from the spec: 100 jobs, several workers, no double-claim."""
    from app.db import database as db_module

    job_ids = []
    async with db_module.session_scope() as session:
        for _ in range(100):
            job = Job(type="sleep", payload={"seconds": 0}, priority=0, max_attempts=1)
            session.add(job)
            job_ids.append(job)
        await session.commit()

    claimed_ids: list = []
    lock = asyncio.Lock()

    async def worker_loop(worker_id: str):
        while True:
            async with db_module.session_scope() as session:
                job = await claim_job(session, worker_id)
                if job is None:
                    return
                async with lock:
                    claimed_ids.append(job.id)
                await execute_handler(job.type, job.payload)
                await complete_job(session, job)

    await asyncio.gather(*(worker_loop(f"worker-{i}") for i in range(10)))

    assert len(claimed_ids) == 100
    assert len(set(claimed_ids)) == 100  # every job claimed exactly once
