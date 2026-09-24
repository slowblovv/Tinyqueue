import random

import pytest

from app.queue.retry import compute_backoff_seconds, should_retry


def test_backoff_matches_spec_example():
    # base_delay=2: attempt 1 -> 2s, 2 -> 4s, 3 -> 8s
    assert compute_backoff_seconds(1, base_delay=2) == 2
    assert compute_backoff_seconds(2, base_delay=2) == 4
    assert compute_backoff_seconds(3, base_delay=2) == 8


def test_backoff_base_delay_one():
    assert compute_backoff_seconds(1, base_delay=1) == 1
    assert compute_backoff_seconds(2, base_delay=1) == 2
    assert compute_backoff_seconds(3, base_delay=1) == 4
    assert compute_backoff_seconds(4, base_delay=1) == 8


def test_backoff_with_jitter_stays_within_bounds():
    rng = random.Random(42)
    delay = compute_backoff_seconds(3, base_delay=1, jitter=0.5, _rand=rng)
    assert 4 <= delay <= 4.5


def test_backoff_rejects_invalid_attempt():
    with pytest.raises(ValueError):
        compute_backoff_seconds(0, base_delay=1)


def test_should_retry_below_max():
    assert should_retry(attempts=2, max_attempts=3) is True


def test_should_retry_at_max_attempts_stops():
    assert should_retry(attempts=3, max_attempts=3) is False


def test_should_retry_exceeding_max_attempts_stops():
    assert should_retry(attempts=4, max_attempts=3) is False
