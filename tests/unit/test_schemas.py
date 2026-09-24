import pytest
from pydantic import ValidationError

from app.schemas.jobs import JobCreate


def test_job_create_defaults():
    job = JobCreate(type="echo", payload={"message": "hi"})
    assert job.priority == 0
    assert job.max_attempts == 3
    assert job.delay_seconds == 0
    assert job.idempotency_key is None


def test_job_create_rejects_negative_priority():
    with pytest.raises(ValidationError):
        JobCreate(type="echo", priority=-1)


def test_job_create_rejects_negative_delay():
    with pytest.raises(ValidationError):
        JobCreate(type="echo", delay_seconds=-5)


def test_job_create_rejects_type_too_long():
    with pytest.raises(ValidationError):
        JobCreate(type="x" * 101)


def test_job_create_rejects_idempotency_key_too_long():
    with pytest.raises(ValidationError):
        JobCreate(type="echo", idempotency_key="x" * 256)
