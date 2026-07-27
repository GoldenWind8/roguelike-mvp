"""Data-access layer: the single place that owns DB connections.

Nothing else in the app creates engines or sessions. Models inherit from
`Base`; request handlers get a session via `Depends(get_session)`. Swapping
SQLite (tests) for Postgres (prod) happens through DATABASE_URL alone — no
code here changes.
"""
import json
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
    import backend.models  # noqa: F401  — registers tables on Base.metadata
    async with engine.begin() as conn:
        # run_sync bridges the sync create_all into the async connection.
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
        # create_all never ALTERs an existing table, so columns added to a
        # model after a db file exists must be backfilled by hand. This is the
        # entire "migration tool" until there's data worth a real one — each
        # entry is (table, column, DDL type, default SQL literal).
        await conn.run_sync(_backfill_columns)


_COLUMN_BACKFILLS = [
    ("players", "inventory", "JSON", "'[]'"),  # M9 loot: the 10-slot pack
    ("players", "hunger", "REAL", "100"),      # hunger meter (LOOT.md Decision 5)
    ("players", "coins", "INTEGER", "30"),     # exploration shops
    ("rooms", "content_id", "VARCHAR", "NULL"),  # authored content identity
    # Living-world story identity. It is populated from the already-validated
    # persona JSON immediately after the column is added.
    ("npcs", "content_id", "VARCHAR", "NULL"),
]


def _backfill_columns(conn) -> None:
    from sqlalchemy import inspect, text
    inspector = inspect(conn)
    for table, column, ddl_type, default in _COLUMN_BACKFILLS:
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type} DEFAULT {default}"
            ))
    _backfill_npc_content_ids(conn)


def _backfill_npc_content_ids(conn) -> None:
    """Give legacy NPC rows their authored identity and enforce uniqueness.

    Numeric row ids are database accidents, so no living-world record may use
    them as story identity. Old saves already carry the stable id inside their
    validated persona JSON; this migration only copies it into an indexed
    column and never rewrites an identity that has already been assigned.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    if not inspector.has_table("npcs"):
        return
    columns = {column["name"] for column in inspector.get_columns("npcs")}
    if "content_id" not in columns:
        return

    rows = conn.execute(text(
        "SELECT id, content_id, persona FROM npcs ORDER BY id"
    )).mappings().all()
    claimed: dict[str, int] = {}
    assignments: list[tuple[int, str]] = []
    for row in rows:
        content_id = row["content_id"]
        if not isinstance(content_id, str) or not content_id.strip():
            persona = row["persona"]
            if isinstance(persona, (bytes, bytearray)):
                persona = persona.decode("utf-8")
            if isinstance(persona, str):
                try:
                    persona = json.loads(persona)
                except (TypeError, ValueError):
                    persona = None
            content_id = persona.get("id") if isinstance(persona, dict) else None
            if isinstance(content_id, str) and content_id.strip():
                content_id = content_id.strip()
                assignments.append((row["id"], content_id))
            else:
                content_id = None

        if content_id is not None:
            previous = claimed.get(content_id)
            if previous is not None:
                raise RuntimeError(
                    "cannot establish stable NPC identities: "
                    f"rows {previous} and {row['id']} both use {content_id!r}"
                )
            claimed[content_id] = row["id"]

    for row_id, content_id in assignments:
        conn.execute(
            text("UPDATE npcs SET content_id = :content_id WHERE id = :row_id"),
            {"content_id": content_id, "row_id": row_id},
        )

    # create_all creates this unique index for fresh databases. Existing tables
    # are invisible to create_all's DDL pass, so add the equivalent after data
    # has been checked and backfilled.
    inspector = inspect(conn)
    has_unique_content_id = any(
        index.get("unique")
        and index.get("column_names") == ["content_id"]
        for index in inspector.get_indexes("npcs")
    )
    if not has_unique_content_id:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ix_npcs_content_id ON npcs (content_id)"
        ))
