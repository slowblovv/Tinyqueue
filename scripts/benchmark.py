"""Load test / benchmark harness.

Creates a batch of jobs via the service layer, then runs an in-process worker
pool against them and reports throughput and latency percentiles.

Usage:

    python -m scripts.benchmark --jobs 10000 --workers 4
    python -m scripts.benchmark --jobs 10000 --workers 1,2,4,8   # sweep

Requires a running PostgreSQL reachable via DATABASE_URL (defaults to the
docker-compose settings). Run migrations first: `alembic upgrade head`.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
import uuid

from app.db.database import dispose_engine, session_scope
from app.db.models import Job
from app.queue.dispatcher import claim_job, complete_job, fail_job
from app.queue.executor import execute_handler

JOB_TYPES = ["echo", "sleep"]


async def seed_jobs(count: int) -> None:
    async with session_scope() as db:
        for i in range(count):
            job_type = random.choice(JOB_TYPES)
            payload = {"message": f"job-{i}"} if job_type == "echo" else {"seconds": 0}
            db.add(Job(type=job_type, payload=payload, priority=random.randint(0, 10)))
            if i % 500 == 0:
                await db.commit()
        await db.commit()


async def run_pool(num_workers: int) -> list[float]:
    """Run workers until the queue drains. Returns per-job latency in ms."""
    latencies: list[float] = []
    lock = asyncio.Lock()

    async def worker_loop(worker_id: str) -> None:
        while True:
            async with session_scope() as db:
                job = await claim_job(db, worker_id)
                if job is None:
                    return
                start = time.perf_counter()
                try:
                    await execute_handler(job.type, job.payload)
                except Exception as exc:  # noqa: BLE001
                    await fail_job(db, job, str(exc), base_delay=1, jitter=0)
                else:
                    await complete_job(db, job)
                elapsed_ms = (time.perf_counter() - start) * 1000
                async with lock:
                    latencies.append(elapsed_ms)

    worker_ids = (f"bench-{uuid.uuid4().hex[:6]}" for _ in range(num_workers))
    await asyncio.gather(*(worker_loop(wid) for wid in worker_ids))
    return latencies


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * (p / 100)
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


async def benchmark(num_jobs: int, worker_counts: list[int]) -> None:
    print(f"{'Workers':>8} {'Jobs/s':>10} {'p50 (ms)':>10} {'p95 (ms)':>10} {'p99 (ms)':>10}")
    for workers in worker_counts:
        await seed_jobs(num_jobs)
        start = time.perf_counter()
        latencies = await run_pool(workers)
        duration = time.perf_counter() - start
        throughput = len(latencies) / duration if duration > 0 else 0
        print(
            f"{workers:>8} {throughput:>10.1f} "
            f"{percentile(latencies, 50):>10.2f} "
            f"{percentile(latencies, 95):>10.2f} "
            f"{percentile(latencies, 99):>10.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="TinyQueue load test / benchmark")
    parser.add_argument("--jobs", type=int, default=10_000)
    parser.add_argument(
        "--workers", type=str, default="1,2,4,8", help="Comma-separated worker counts to sweep"
    )
    args = parser.parse_args()
    worker_counts = [int(w) for w in args.workers.split(",")]

    async def _run() -> None:
        await benchmark(args.jobs, worker_counts)
        await dispose_engine()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
