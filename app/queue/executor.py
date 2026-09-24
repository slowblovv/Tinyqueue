"""Job handler registry.

Handlers are plain sync-or-async callables `handler(payload: dict) -> Any`.
We never execute arbitrary code supplied in a job payload — the job's `type`
is only ever used to look up a pre-registered handler by name.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

Handler = Callable[[dict], Any]

HANDLERS: dict[str, Handler] = {}


def register(name: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn

    return decorator


class UnknownHandlerError(Exception):
    pass


class HandlerFailure(Exception):
    pass


async def execute_handler(job_type: str, payload: dict) -> Any:
    handler = HANDLERS.get(job_type)
    if handler is None:
        raise UnknownHandlerError(f"No handler registered for job type '{job_type}'")

    result = handler(payload)
    if inspect.isawaitable(result):
        result = await result
    return result


# ---------------------------------------------------------------------------
# Built-in demo handlers
# ---------------------------------------------------------------------------


@register("sleep")
async def handle_sleep(payload: dict) -> dict:
    """{"seconds": 2}"""
    seconds = float(payload.get("seconds", 0))
    await asyncio.sleep(seconds)
    return {"slept": seconds}


@register("echo")
async def handle_echo(payload: dict) -> dict:
    """{"message": "hello"}"""
    return {"echo": payload.get("message")}


@register("fail")
async def handle_fail(payload: dict) -> None:
    """{"error": "test failure"} — used to exercise the retry path."""
    raise HandlerFailure(str(payload.get("error", "forced failure")))


@register("send_email")
async def handle_send_email(payload: dict) -> dict:
    """Stub handler: {"to": "...", "subject": "..."}. Does not actually send mail."""
    return {"sent_to": payload.get("to"), "subject": payload.get("subject")}


@register("resize_image")
async def handle_resize_image(payload: dict) -> dict:
    """Stub handler: {"path": "...", "width": ..., "height": ...}."""
    return {"resized": payload.get("path")}


@register("cleanup")
async def handle_cleanup(payload: dict) -> dict:
    """Stub handler: {"path": "..."}."""
    return {"cleaned": payload.get("path")}
