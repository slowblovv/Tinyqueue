"""Shared pytest fixtures for integration and e2e tests using a real PostgreSQL database.

These tests require a real PostgreSQL database. Point `TEST_DATABASE_URL`
(or `DATABASE_URL`) at a disposable database before running, e.g.:

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://tinyqueue:tinyqueue@localhost:5432/tinyqueue_test \
        pytest tests/integration
"""

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import database as db_module
from app.db.database import Base
from app.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://tinyqueue:tinyqueue@localhost:5432/tinyqueue_test"
    ),
)


@pytest_asyncio.fixture
async def engine():
    test_engine = create_async_engine(TEST_DATABASE_URL, future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Point the app's global engine/session-factory at the test database.
    db_module._engine = test_engine
    db_module._session_factory = async_sessionmaker(
        bind=test_engine, expire_on_commit=False
    )

    yield test_engine

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()
    db_module._engine = None
    db_module._session_factory = None


@pytest_asyncio.fixture
async def db_session(engine):
    async with db_module.session_scope() as session:
        yield session


@pytest_asyncio.fixture
async def client(engine):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
