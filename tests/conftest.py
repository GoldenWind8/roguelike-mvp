"""Shared test fixtures.

The `session` fixture here is the one every later M1 issue (#20-#24) reuses
to test models against a real-but-disposable database.
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.db import Base


@pytest_asyncio.fixture
async def session():
    """A clean DB per test, in memory, torn down afterwards.

    Why a separate engine instead of backend.db.engine?
      - Tests must never touch the real game.db file — they get their own.
      - In-memory SQLite lives only as long as its connection. StaticPool
        forces every checkout to reuse ONE connection, so the tables made
        by create_all are still there when the session queries them.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Build the schema from whatever models have been imported (none yet).
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))

    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    await engine.dispose()
