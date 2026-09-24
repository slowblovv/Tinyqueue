from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import queue_depth
from app.db.database import get_db
from app.db.models import Job, JobStatus

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(db: AsyncSession = Depends(get_db)) -> Response:
    depth = await db.scalar(
        select(func.count()).select_from(Job).where(Job.status == JobStatus.PENDING)
    )
    queue_depth.set(depth or 0)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
