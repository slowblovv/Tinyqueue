from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import JobStatus
from app.schemas.jobs import JobCreate, JobListQuery, JobListResponse, JobResponse
from app.services import jobs as jobs_service

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)) -> JobResponse:
    job = await jobs_service.create_job(db, payload)
    return JobResponse.model_validate(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    status_: JobStatus | None = Query(default=None, alias="status"),
    type: str | None = Query(default=None),
    priority: int | None = Query(default=None),
    created_after=Query(default=None),
    created_before=Query(default=None),
    limit: int = Query(default=20, ge=1),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    query = JobListQuery(
        status=status_,
        type=type,
        priority=priority,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )
    items, total = await jobs_service.list_jobs(db, query)
    return JobListResponse(
        items=[JobResponse.model_validate(j) for j in items],
        limit=query.limit,
        offset=query.offset,
        total=total,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> JobResponse:
    job = await jobs_service.get_job(db, job_id)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> JobResponse:
    job = await jobs_service.cancel_job(db, job_id)
    return JobResponse.model_validate(job)
