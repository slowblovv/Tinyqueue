"""Structured (JSON) logging configuration.

Every log record emitted through the loggers configured here is a single-line
JSON object, e.g.:

    {"level": "INFO", "event": "job_completed", "job_id": "...",
     "worker_id": "...", "duration_ms": 142}

Call `configure_logging()` once at process startup (API and worker both do this).
Use `get_logger(__name__)` and pass structured fields via `extra={"event": ..., ...}`.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from pythonjsonlogger import json as jsonlogger

_CONFIGURED = False

# Fields that must never be written to logs even if present in `extra`.
_REDACTED_KEYS = {"payload", "password", "token", "secret", "authorization"}


class RedactingJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record.setdefault("level", record.levelname)
        for key in list(log_record.keys()):
            if key.lower() in _REDACTED_KEYS:
                log_record[key] = "***redacted***"


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    formatter = RedactingJsonFormatter(
        "%(timestamp)s %(level)s %(name)s %(message)s",
        rename_fields={"timestamp": "timestamp"},
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Quiet down noisy third-party loggers a bit.
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    """Emit a structured log line: {"event": event, ...fields}."""
    fields.pop("payload", None)  # never log raw payload bodies
    logger.log(level, event, extra={"event": event, **fields})
