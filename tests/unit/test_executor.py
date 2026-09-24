import pytest

from app.queue.executor import HANDLERS, HandlerFailure, UnknownHandlerError, execute_handler


def test_builtin_handlers_are_registered():
    for name in ("sleep", "echo", "fail", "send_email", "resize_image", "cleanup"):
        assert name in HANDLERS


async def test_echo_handler_returns_message():
    result = await execute_handler("echo", {"message": "hello"})
    assert result == {"echo": "hello"}


async def test_sleep_handler_completes():
    result = await execute_handler("sleep", {"seconds": 0})
    assert result == {"slept": 0.0}


async def test_fail_handler_raises_handler_failure():
    with pytest.raises(HandlerFailure):
        await execute_handler("fail", {"error": "boom"})


async def test_unknown_job_type_raises():
    with pytest.raises(UnknownHandlerError):
        await execute_handler("does_not_exist", {})
