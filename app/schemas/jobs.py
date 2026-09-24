"""Pydantic schemas for the jobs API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.db.models import JobStatus


class JobCreate(BaseModel):
    type: str = Field(..., min_length=1)
    payload: dict = Field(default_factory=dict)
    priority: int = Field(default=0, ge=0, le=10)
    max_attempts: int = Field(default=3, ge=1, le=50)
    delay_seconds: float = Field(default=0, ge=0)
    timeout_seconds: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(default=None)

    @field_validator("type")
    @classmethod
    def validate_type_length(cls, v: str) -> str:
        settings = get_settings()
        if len(v) > settings.max_job_type_length:
            raise ValueError(
                f"type must be at most {settings.max_job_type_length} characters"
            )
        return v

    @field_validator("idempotency_key")
    @classmethod
    def validate_key_length(cls, v: str | None) -> str | None:
        if v is None:
            return v
        settings = get_settings()
        if len(v) > settings.max_idempotency_key_length:
            raise ValueError(
                f"idempotency_key must be at most {settings.max_idempotency_key_length} characters"
            )
        return v


class JobResponse(BaseModel):
    id: uuid.UUID
    type: str
    status: JobStatus
    priority: int
    attempts: int
    max_attempts: int
    available_at: datetime
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    last_error: str | None = None
    worker_id: str | None = None
    idempotency_key: str | None = None

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    items: list[JobResponse]
    limit: int
    offset: int
    total: int


class JobListQuery(BaseModel):
    status: JobStatus | None = None
    type: str | None = None
    priority: int | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = Field(default=20, ge=1)
    offset: int = Field(default=0, ge=0)

    @field_validator("limit")
    @classmethod
    def cap_limit(cls, v: int) -> int:
        settings = get_settings()
        return min(v, settings.max_page_limit)
