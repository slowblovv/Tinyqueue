"""Retry / exponential backoff calculation.

    delay = base_delay * 2^(attempt - 1) [+ random(0, jitter)]

attempt is 1-indexed (the attempt number that just failed).
"""

from __future__ import annotations

import random


def compute_backoff_seconds(
    attempt: int,
    base_delay: float,
    jitter: float = 0.0,
    _rand: random.Random | None = None,
) -> float:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if base_delay < 0:
        raise ValueError("base_delay must be >= 0")
    if jitter < 0:
        raise ValueError("jitter must be >= 0")

    delay = base_delay * (2 ** (attempt - 1))
    if jitter > 0:
        rng = _rand or random
        delay += rng.uniform(0, jitter)
    return delay


def should_retry(attempts: int, max_attempts: int) -> bool:
    """`attempts` is the count *after* incrementing for the failed try."""
    return attempts < max_attempts
