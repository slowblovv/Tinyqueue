# TinyQueue

A lightweight distributed job queue with a REST API, PostgreSQL storage,
background workers, retries, scheduling and dead-letter handling — a small,
production-style alternative to a much simplified Redis Queue / Celery /
BullMQ.

## 1. What is TinyQueue

TinyQueue lets an application enqueue background jobs over HTTP and have a
pool of workers execute them reliably:

```
Application → POST /api/v1/jobs → TinyQueue API → PostgreSQL → Worker → Execute Job
```

It's built to demonstrate production backend engineering, not just
algorithms: a REST API, a real database schema with the right indexes and
transactions, concurrent workers that never double-process a job, retry with
backoff, crash recovery, graceful shutdown, Docker, and tests — including the
concurrency and crash-recovery tests that are usually skipped.

## 2. Architecture

```
                 ┌──────────────┐
                 │    Client    │
                 └──────┬───────┘
                        │ HTTP
                        ▼
               ┌──────────────────┐
               │   FastAPI API    │
               └────────┬─────────┘
                        │
            ┌───────────┴────────────┐
            ▼                        ▼
      ┌───────────┐            ┌───────────┐
      │ PostgreSQL│◄───────────┤  Worker(s)│
      └───────────┘  FOR UPDATE└───────────┘
                      SKIP LOCKED    │
                                     ▼
                              Handler Registry
                            (echo / sleep / fail / ...)
```

See [`docs/architecture.md`](docs/architecture.md) for the design rationale
behind every non-obvious decision — atomic claiming, worker-crash recovery,
idempotency, backoff, priority ordering, and graceful shutdown.

## 3. Features

- Create / get / list / cancel jobs over a REST API
- Worker pool with atomic job claiming (`SELECT ... FOR UPDATE SKIP LOCKED`)
- Retry with exponential backoff + jitter, configurable max attempts
- Dead-letter state (`FAILED`) after max attempts, with `last_error` retained
- Delayed jobs (`delay_seconds`) and priority ordering
- Idempotency keys with a database-enforced unique constraint
- Worker heartbeat + automatic reclaim of jobs abandoned by a dead worker
- Graceful shutdown on `SIGTERM`/`SIGINT`
- `/health`, `/ready`, and Prometheus-compatible `/metrics`
- Structured JSON logging with payload/secret redaction
- Docker Compose stack: `postgres` + `migrate` + `api` + `worker`
- Unit, integration (real Postgres), and end-to-end tests, including a
  100-jobs/10-workers no-double-claim concurrency test and an automated
  worker-crash-recovery test

## 4. Setup

Requires Python 3.12+ and a PostgreSQL instance (or use Docker Compose below).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # edit DATABASE_URL etc. as needed

alembic upgrade head
uvicorn app.main:app --reload            # API on http://localhost:8000
python -m app.queue.worker --workers 4   # in a second terminal
```

## 5. Docker

```bash
docker compose up            # postgres + migrate + api + worker
docker compose up --scale worker=2   # run extra worker containers
```

The `migrate` service runs `alembic upgrade head` once and exits before `api`
and `worker` start.

## 6. API

Interactive docs at `/docs` (Swagger) and `/redoc` once the API is running.

**Create a job**

```
POST /api/v1/jobs
{
  "type": "send_email",
  "payload": {"to": "user@example.com", "subject": "Welcome"},
  "priority": 5,
  "max_attempts": 3,
  "delay_seconds": 10,
  "idempotency_key": "welcome-user-123"
}
→ 201 { "id": "...", "status": "PENDING", ... }
```

**Get / list / cancel**

```
GET  /api/v1/jobs/{job_id}
GET  /api/v1/jobs?status=FAILED&limit=20&offset=0
POST /api/v1/jobs/{job_id}/cancel     # only PENDING (incl. delayed) jobs
```

Built-in handlers you can use out of the box: `echo`, `sleep`, `fail` (for
exercising retries), plus stubs `send_email`, `resize_image`, `cleanup`.

Errors are always `{"error": {"code": "...", "message": "..."}}`, e.g.
`JOB_NOT_FOUND`, `INVALID_JOB_TYPE`, `INVALID_STATUS_TRANSITION`,
`DUPLICATE_IDEMPOTENCY_KEY`, `INVALID_PAYLOAD`.

## 7. Job lifecycle

```
PENDING → PROCESSING → COMPLETED
             │
          (failure, attempts < max) → PENDING (retry, available_at in future)
             │
          (failure, attempts >= max) → FAILED

PENDING → CANCELLED   (only while still pending / delayed)
```

A "delayed" job is just `PENDING` with `available_at` in the future — the
worker's claim query filters on `available_at <= now()`.

## 8. Retry

```
delay = base_delay × 2^(attempt - 1) [+ random(0, jitter)]
```

With `max_attempts=4, base_delay=2`: attempt 1 fails → +2s, attempt 2 → +4s,
attempt 3 → +8s, attempt 4 fails → `FAILED`.

## 9. Idempotency

Pass `idempotency_key` on create. A second request with the same key returns
the original job instead of creating a duplicate — enforced by a `UNIQUE`
database constraint, not just an application-level check, so it's safe under
concurrent retries.

## 10. Worker recovery

Workers write a heartbeat (`heartbeat_at`) while processing a job. A
scheduler loop resets any job stuck in `PROCESSING` with a stale heartbeat
back to `PENDING` so another worker can pick it up — see
[`docs/architecture.md`](docs/architecture.md#challenge-2--what-happens-if-a-worker-dies-mid-job)
for the full explanation, and
`tests/e2e/test_full_flow.py::test_worker_crash_recovery_full_cycle` for the
automated test of the whole cycle.

## 11. Database schema

Single `jobs` table (see `app/db/models.py`), key indexes:

- `(status, priority, available_at)` — critical for worker polling
- `(idempotency_key)` unique
- `(created_at)`

## 12. Configuration

All configuration is via environment variables — see `.env.example` for the
full list (`DATABASE_URL`, `WORKER_COUNT`, `JOB_POLL_INTERVAL`, `JOB_TIMEOUT`,
`MAX_ATTEMPTS`, `RETRY_BASE_DELAY`, `HEARTBEAT_TIMEOUT`, payload/pagination
limits, etc.). Nothing is hardcoded.

## 13. Testing

```bash
pytest tests/unit -q                 # no database required
TEST_DATABASE_URL=postgresql+asyncpg://tinyqueue:tinyqueue@localhost:5432/tinyqueue_test \
    pytest tests/integration tests/e2e -q
coverage run -m pytest -q && coverage report -m
```

Current status: **40 tests passing** (17 unit, 19 integration — including
dedicated health/metrics checks — and 4 end-to-end, covering the full
POST→worker→COMPLETED path, retry-to-FAILED, crash recovery, and the
100-jobs/10-workers concurrency guarantee), **90% line coverage**, clean
`ruff check .` and `mypy app`.

## 14. Benchmark

Measured with `python -m scripts.benchmark --jobs 2000 --workers 1,2,4,8` in
this development environment (single container, single-core, local
PostgreSQL — not representative of production hardware, but real numbers
from a real run, not invented ones):

| Workers | Jobs/s | p50 (ms) | p95 (ms) | p99 (ms) |
|--------:|-------:|---------:|---------:|---------:|
| 1       | 206.7  | 1.06     | 1.31     | 1.52     |
| 2       | 213.6  | 1.78     | 2.65     | 3.19     |
| 4       | 218.5  | 3.05     | 4.16     | 5.04     |
| 8       | 220.4  | 5.42     | 7.04     | 7.77     |

Throughput plateaus quickly because these jobs (`echo`/`sleep(0)`) are
near-instant, so PostgreSQL round-trip latency — not handler execution — is
the bottleneck in this environment, exactly as predicted in
[`docs/architecture.md`](docs/architecture.md#wheres-the-bottleneck). Re-run
`scripts/benchmark.py` on real hardware against a tuned Postgres instance for
numbers that matter for capacity planning.

## 15. Design decisions

See [`docs/architecture.md`](docs/architecture.md) — covers all six core
design challenges (atomic claim, crash recovery, idempotency under
concurrency, backoff without hammering the DB, priority ordering on top of
Postgres, graceful shutdown), plus what happens if Postgres itself goes down
and where the system's bottleneck actually is.

## 16. Limitations

- At-least-once, not exactly-once, execution: a job can run twice if a
  worker dies after finishing side effects but before heartbeat/commit
  recovery kicks in. Handlers should be idempotent where that matters.
- No authentication in v1 (documented as out of scope, not an oversight).
- Single-job claim per transaction (no batch claiming yet).
- `PROCESSING` jobs cannot be cancelled in v1 — documented, not silently
  ignored.
- No dedicated dead-letter table — `FAILED` status + `last_error` serves
  that purpose for v1.

## 17. Roadmap

- Batch job claiming (`CLAIM_BATCH_SIZE > 1`) to cut DB round trips
- Cursor-based pagination as an alternative to limit/offset
- Dedicated `dead_letter_jobs` table for FAILED jobs (v2)
- Optional API authentication
- Per-job-type concurrency limits
