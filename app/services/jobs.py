"""Business logic for job CRUD operations (used by the API layer)."""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import InvalidJobType, InvalidStatusTransition, JobNotFound, PayloadTooLarge
from app.core.metrics import jobs_cancelled_total, jobs_created_total
from app.db.models import Job, JobStatus
from app.queue.executor import HANDLERS
from app.schemas.jobs import JobCreate, JobListQuery


def _payload_size_bytes(payload: dict) -> int:
    import json

    return sys.getsizeof(json.dumps(payload))


async def create_job(db: AsyncSession, data: JobCreate) -> Job:
    if data.type not in HANDLERS:
        raise InvalidJobType(f"Unknown job type '{data.type}'")

    settings = get_settings()
    if _payload_size_bytes(data.payload) > settings.max_payload_bytes:
        raise PayloadTooLarge(
            f"payload exceeds max size of {settings.max_payload_bytes} bytes"
        )

    # Idempotency: if a job with this key already exists, return it unchanged.
    if data.idempotency_key:
        existing = await db.scalar(
            select(Job).where(Job.idempotency_key == data.idempotency_key)
        )
        if existing is not None:
            return existing

    now = datetime.now(UTC)
    available_at = now + timedelta(seconds=data.delay_seconds)

    job = Job(
        type=data.type,
        payload=data.payload,
        priority=data.priority,
        max_attempts=data.max_attempts,
        timeout_seconds=data.timeout_seconds,
        idempotency_key=data.idempotency_key,
        available_at=available_at,
        status=JobStatus.PENDING,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        # Race: another request created a job with the same idempotency_key
        # concurrently. Fall back to returning the existing row.
        await db.rollback()
        if data.idempotency_key:
            existing = await db.scalar(
                select(Job).where(Job.idempotency_key == data.idempotency_key)
            )
            if existing is not None:
                return existing
        raise
    await db.refresh(job)
    jobs_created_total.inc()
    return job


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise JobNotFound(f"Job {job_id} does not exist")
    return job


async def list_jobs(db: AsyncSession, query: JobListQuery) -> tuple[list[Job], int]:
    stmt = select(Job)
    count_stmt = select(func.count()).select_from(Job)

    conditions = []
    if query.status is not None:
        conditions.append(Job.status == query.status)
    if query.type is not None:
        conditions.append(Job.type == query.type)
    if query.priority is not None:
        conditions.append(Job.priority == query.priority)
    if query.created_after is not None:
        conditions.append(Job.created_at >= query.created_after)
    if query.created_before is not None:
        conditions.append(Job.created_at <= query.created_before)

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    stmt = stmt.order_by(Job.created_at.desc()).limit(query.limit).offset(query.offset)

    items = (await db.scalars(stmt)).all()
    total = await db.scalar(count_stmt) or 0
    return list(items), total


async def cancel_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await get_job(db, job_id)

    if job.status != JobStatus.PENDING:
        # Covers both delayed jobs (still PENDING, available_at in the future)
        # and disallowed transitions from PROCESSING/COMPLETED/FAILED/CANCELLED.
        raise InvalidStatusTransition(
            f"Cannot cancel job in status {job.status}. "
            "Only PENDING (including delayed) jobs may be cancelled; "
            "cancellation of PROCESSING jobs is not supported in v1."
        )

    job.status = JobStatus.CANCELLED
    job.cancelled_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(job)
    jobs_cancelled_total.inc()
    return job
