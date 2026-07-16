"""Shared test fixtures.

The `session` fixture here is the one every later M1 issue (#20-#24) reuses
to test models against a real-but-disposable database.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401 — register tables on Base.metadata before create_all
from backend.db import Base
from backend.room_loader import EnemySpawn, RoomTemplate


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


@pytest.fixture
def make_template():
    """Factory for a synthetic in-memory RoomTemplate — pure-engine tests (door
    events, attach/detach) need no DB. Default shape: a width x height room
    with border walls; tiles listed in `connections` or `doors` are passable
    gaps in the border (doors lead somewhere, plain doors lead nowhere).

    There is no `mode` knob — mode is derived from who is present (M7). A test
    that wants a combat room seeds a hostile via `enemies` (grid positions);
    an empty room is an exploration room, same as in production."""
    def _make(
        width: int = 5,
        height: int = 5,
        spawn_points: list[tuple[int, int]] | None = None,
        connections: dict[tuple[int, int], int] | None = None,
        doors: tuple[tuple[int, int], ...] = (),
        capacity: int | None = None,
        enemies: tuple[tuple[int, int], ...] = (),
    ) -> RoomTemplate:
        spawn_points = spawn_points or [(1, 1), (2, 1)]
        connections = connections or {}
        border = (
            {(x, y) for x in range(width) for y in (0, height - 1)}
            | {(x, y) for y in range(height) for x in (0, width - 1)}
        )
        walls = border - set(connections) - set(doors)
        return RoomTemplate(
            room_id=1,
            room_name="Test Room",
            width=width,
            height=height,
            spawn_points=list(spawn_points),
            walls=walls,
            enemies=[
                EnemySpawn(name="Goblin", hp=10, attack_damage=1, defense=0, position=pos)
                for pos in enemies
            ],
            capacity=capacity if capacity is not None else len(spawn_points),
            connections=dict(connections),
        )
    return _make
