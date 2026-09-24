from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.db.database import check_db_connection

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict:
    db_ok = await check_db_connection()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "database": "unavailable"}
    return {"status": "ready", "database": "ok"}
