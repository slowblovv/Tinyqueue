from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, jobs, metrics
from app.core.config import get_settings
from app.core.errors import TinyQueueError, tinyqueue_error_handler, unhandled_error_handler
from app.core.logging import configure_logging, get_logger, log_event
from app.db.database import dispose_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log_event(logger, "api_started")
    try:
        yield
    finally:
        # Graceful shutdown: stop accepting work, drain, close DB connections.
        log_event(logger, "api_stopping")
        await dispose_engine()
        log_event(logger, "api_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="TinyQueue",
        description=(
            "A lightweight distributed job queue with a REST API, PostgreSQL storage, "
            "background workers, retries, scheduling and dead-letter handling."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_exception_handler(TinyQueueError, tinyqueue_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(jobs.router)
    app.include_router(health.router)
    app.include_router(metrics.router)

    return app


app = create_app()
