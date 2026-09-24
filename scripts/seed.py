"""Seed the queue with a handful of demo jobs for manual testing.

Usage:

    python -m scripts.seed
    python -m scripts.seed --count 20
"""

from __future__ import annotations

import argparse
import asyncio

from app.db.database import dispose_engine, session_scope
from app.db.models import Job


async def seed(count: int) -> None:
    async with session_scope() as db:
        db.add(Job(type="echo", payload={"message": "hello from seed.py"}, priority=5))
        db.add(Job(type="sleep", payload={"seconds": 2}, priority=1))
        db.add(Job(type="fail", payload={"error": "seeded failure"}, max_attempts=2))
        for i in range(count):
            db.add(Job(type="echo", payload={"message": f"bulk-{i}"}, priority=i % 10))
        await db.commit()
    print(f"Seeded 3 demo jobs + {count} bulk jobs.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed TinyQueue with demo jobs")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    async def _run() -> None:
        await seed(args.count)
        await dispose_engine()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
