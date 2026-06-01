"""Data-access layer: the single place that owns DB connections.

Nothing else in the app creates engines or sessions. Models inherit from
`Base`; request handlers get a session via `Depends(get_session)`. Swapping
SQLite (tests) for Postgres (prod) happens through DATABASE_URL alone — no
code here changes.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.config import DATABASE_URL


# The engine owns the connection pool. One per process — create it once and
# reuse it; it's not a connection itself, it hands them out and recycles them.
engine = create_async_engine(DATABASE_URL, echo=False)


# Factory for sessions (the Unit of Work — a session batches changes and
# commits them as one transaction).
#
# expire_on_commit=False is the key async gotcha: by default SQLAlchemy
# "expires" objects after commit, so the next attribute access silently
# re-fetches from the DB. In async that lazy IO happens outside an `await`
# and raises MissingGreenlet. Turning it off keeps committed objects usable.
SessionMaker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base. Every model subclasses this; create_all() builds
    tables from whatever subclasses Base.metadata has seen at import time."""
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a session, always close it.

    Decision A — this does NOT commit. Endpoints commit explicitly so the
    write is visible where it happens. We revisit auto-commit once the
    boilerplate earns it.
    """
    async with SessionMaker() as session:
        yield session


async def init_db() -> None:
    """Create all tables from the models registered on Base.metadata.

    No migration tool yet (BACKEND.md: recreate-from-models until there's
    data worth keeping). Importing the models module here guarantees they're
    registered before create_all runs — until models exist this is a no-op.
    """
    # import backend.models  # noqa: F401  — uncomment once models exist
    async with engine.begin() as conn:
        # run_sync bridges the sync create_all into the async connection.
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
