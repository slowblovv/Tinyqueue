# TinyQueue — Architecture

This document explains the *why* behind TinyQueue's design, focused on the
six design challenges that define the project.

## Why PostgreSQL as the only infrastructure dependency?

The spec deliberately excludes Redis, Kafka, and RabbitMQ. PostgreSQL already
gives us everything a queue needs: durable storage, transactions, row-level
locking (`FOR UPDATE SKIP LOCKED`), and indexes for efficient polling. Adding
a second infrastructure dependency would mean two failure domains, two
consistency models, and two things to operate — for a workload (background
jobs, not millions of msgs/sec) where Postgres is genuinely sufficient. The
point of this project is demonstrating an understanding of queue semantics,
not integrating a message broker.

## Challenge #1 — How do two workers avoid claiming the same job?

Without care, two workers can both run:

```sql
SELECT * FROM jobs WHERE status = 'PENDING' ORDER BY priority DESC LIMIT 1;
```

and get the same row before either has written back `PROCESSING`. TinyQueue
solves this with a single atomic transaction:

```sql
BEGIN;
SELECT * FROM jobs
WHERE status = 'PENDING' AND available_at <= now()
ORDER BY priority DESC, available_at ASC, created_at ASC
LIMIT 1
FOR UPDATE SKIP LOCKED;

UPDATE jobs SET status = 'PROCESSING', attempts = attempts + 1, ...
WHERE id = :id;
COMMIT;
```

`FOR UPDATE` takes a row lock; `SKIP LOCKED` tells Postgres "if the top
candidate row is already locked by another transaction, skip it and consider
the next one" instead of blocking. So Worker A locks job 1 and starts
processing it; Worker B's `SELECT` simply never sees job 1 as a candidate and
moves on to job 2. Neither worker blocks, and no two workers can complete the
`SELECT ... FOR UPDATE` on the same row before one of them commits.

This is implemented in `app/queue/dispatcher.py::claim_job` and is exercised
directly by `tests/integration/test_dispatcher.py::test_two_concurrent_claims_never_get_same_job`
and end-to-end by the 100-jobs/10-workers test in `tests/e2e/test_full_flow.py`.

## Challenge #2 — What happens if a worker dies mid-job?

A job marked `PROCESSING` by a worker that then crashes (OOM-killed, deployed
over, etc.) would otherwise sit in `PROCESSING` forever — a job "black hole."

TinyQueue's answer is a **heartbeat**: while a worker executes a job, a
background task (`Worker._heartbeat_loop`) updates `heartbeat_at` on that row
every `HEARTBEAT_INTERVAL` seconds. A separate **scheduler loop**
(`app/queue/scheduler.py`), running once per worker process, periodically
scans for jobs where `status = PROCESSING` and `heartbeat_at` is older than
`HEARTBEAT_TIMEOUT`, and resets them to `PENDING` (clearing `worker_id`,
`locked_at`, `heartbeat_at`). The next healthy worker then claims and retries
it through the normal claim path.

This means a job may occasionally run twice if a worker dies partway through
side effects (at-least-once, not exactly-once, execution) — which is why
handlers should be designed to be idempotent where that matters. This
trade-off is stated explicitly rather than glossed over.

## Challenge #3 — How do we avoid duplicate jobs from client retries?

A client that times out waiting for a response to `POST /jobs` doesn't know
if the job was created or not, and a naive retry would create it twice
(e.g. sending the same payment-processing job twice).

TinyQueue exposes an `idempotency_key` field with a `UNIQUE` database
constraint. `create_job` first checks for an existing job with that key and
returns it unchanged if found; if two requests race past that check
concurrently, the second `INSERT` hits the unique constraint, and
`create_job` catches the resulting `IntegrityError`, rolls back, and returns
the row the other request created. The uniqueness guarantee comes from the
database, not from the read-then-write check in application code, which is
what makes it safe under concurrency.

## Challenge #4 — How do we retry without hammering the database?

Immediate, unconditional retries on failure would turn a transient outage
(e.g. a downstream email provider blipping) into a self-inflicted DDoS
against that same downstream service, and would spin workers in a tight
failure loop.

TinyQueue uses **exponential backoff with jitter**
(`app/queue/retry.py::compute_backoff_seconds`):

```
delay = base_delay * 2^(attempt - 1) [+ random(0, jitter)]
```

A failed job is set back to `PENDING` with `available_at` pushed into the
future by that delay; the claim query's `available_at <= now()` filter means
no worker will pick it up before then. Jitter spreads out retries that would
otherwise all land on the same tick if many jobs failed at once (thundering
herd). Workers also don't poll continuously — `JOB_POLL_INTERVAL` bounds how
often an idle worker re-queries when there's no work, avoiding a hot polling
loop.

## Challenge #5 — How do we get a priority queue out of PostgreSQL?

No dedicated priority-queue data structure is needed — an index does the
job. The claim query orders by:

```
priority DESC, available_at ASC, created_at ASC
```

and the composite index `(status, priority, available_at)` lets Postgres
satisfy `WHERE status = 'PENDING' AND available_at <= now() ORDER BY
priority DESC, available_at ASC` with an index scan instead of a sequential
scan + sort, even as the table grows. This is the single most
performance-critical index in the schema, which is why it's called out
explicitly in the model and the initial migration.

## Challenge #6 — How do we stop a worker cleanly?

An abrupt `kill -9` mid-job leaves that job to be recovered via the
heartbeat path above (correct, but slower and noisier than necessary). For a
normal deploy or scale-down, TinyQueue instead handles `SIGTERM`/`SIGINT`:

- The worker pool installs signal handlers that set an `asyncio.Event`.
- Each worker's poll loop checks that event *between* jobs — it will finish
  a job currently in flight but will not claim a new one once the event is
  set.
- The FastAPI app's `lifespan` context similarly stops accepting new
  connections and disposes the database engine on shutdown.

The result: `docker compose stop` / a Kubernetes pod termination gives
workers a chance to finish their current job and exit cleanly, rather than
relying solely on crash recovery.

## What happens if PostgreSQL goes down?

- **API**: `/ready` starts failing (it runs `SELECT 1`); requests to create
  or query jobs will error until the database is back. The API does not
  crash — SQLAlchemy's `pool_pre_ping=True` detects dead connections and
  reconnects once Postgres recovers.
- **Workers**: claim/heartbeat/complete calls will raise; a worker loop
  iteration fails, the exception is not silently swallowed at the loop
  level, and the worker retries on its next poll tick once the database is
  reachable again. In-flight jobs whose heartbeat can't be written will
  eventually be picked up by the reclaim logic once things recover, exactly
  as if the worker itself had died.
- **No jobs are lost** as long as Postgres's own durability (WAL, backups)
  is intact — TinyQueue has no in-memory queue state that would be lost on a
  database outage.

## Where's the bottleneck?

At the scale this project targets (background jobs, not high-frequency
trading), PostgreSQL itself is the bottleneck: every claim, heartbeat, and
completion is a round trip and a write. The main levers to scale further,
roughly in order of effort:

1. **Batch claiming** (claim up to N jobs per transaction) — reduces the
   number of round trips per job. Called out as an optional v2 improvement
   (`CLAIM_BATCH_SIZE`); v1 intentionally claims one job at a time for a
   simpler, easier-to-reason-about code path.
2. **Fewer heartbeats** for short jobs — a job that finishes in under one
   heartbeat interval never needs a heartbeat write at all; only
   long-running jobs need one.
3. **Read replicas / connection pooling (pgbouncer)** if the number of
   worker connections becomes the limiting factor rather than write
   throughput.
4. Eventually, horizontal partitioning of the `jobs` table or a dedicated
   queue technology (Redis/Kafka) — but that's explicitly out of scope for
   what this project is trying to demonstrate.

## Status machine

```
PENDING ──(claim)──> PROCESSING ──(success)──> COMPLETED
   ▲                      │
   │                   (failure)
   │                      │
   └──(attempts < max)────┤
                          │
                     (attempts >= max)
                          │
                          ▼
                        FAILED

PENDING ──(cancel)──> CANCELLED   [only while still PENDING/delayed]
```

`SCHEDULED` is not a distinct persisted state: a delayed job is simply
`PENDING` with `available_at` set in the future. The claim query's
`available_at <= now()` filter is what makes that distinction behavioral
rather than requiring a separate status.
