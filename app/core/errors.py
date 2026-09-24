"""Unified application error types.

All API errors are returned as:

    {"error": {"code": "JOB_NOT_FOUND", "message": "Job does not exist"}}

Never leak stack traces to the client.
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse


class TinyQueueError(Exception):
    """Base class for all application errors that map to a client response."""

    code: str = "INTERNAL_ERROR"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str | None = None):
        self.message = message or self.__class__.__doc__ or self.code
        super().__init__(self.message)


class JobNotFound(TinyQueueError):
    """Job does not exist"""

    code = "JOB_NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND


class InvalidJobType(TinyQueueError):
    """Job type is not registered in the handler registry"""

    code = "INVALID_JOB_TYPE"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class InvalidStatusTransition(TinyQueueError):
    """The requested operation is not valid for the job's current status"""

    code = "INVALID_STATUS_TRANSITION"
    status_code = status.HTTP_409_CONFLICT


class DuplicateIdempotencyKey(TinyQueueError):
    """A job with this idempotency key already exists"""

    code = "DUPLICATE_IDEMPOTENCY_KEY"
    status_code = status.HTTP_409_CONFLICT


class InvalidPayload(TinyQueueError):
    """Job payload failed validation"""

    code = "INVALID_PAYLOAD"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class PayloadTooLarge(TinyQueueError):
    """Job payload exceeds the maximum allowed size"""

    code = "PAYLOAD_TOO_LARGE"
    status_code = status.HTTP_413_CONTENT_TOO_LARGE


async def tinyqueue_error_handler(request: Request, exc: TinyQueueError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never leak internal details (stack traces, exception repr) to the client.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}},
    )
