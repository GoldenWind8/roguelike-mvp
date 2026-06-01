"""Factory/mapper: a stored `Room` row -> a runtime `LevelData`.

This is the seam between the persistence model (ORM `Room`/`EnemyDef`, which
know about the DB) and the domain model (`LevelData`/`WorldState`, which know
about gameplay and nothing about the DB). It is the ONLY place that reads the
DB to build a world — combat then runs purely from memory (BACKEND.md: DB at
the edges, never in the hot loop).
"""
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EnemyDef, Room, TileType


@dataclass
class EnemySpawn:
    """A fully-resolved enemy ready to place — stats already looked up from the
    enemy_defs table, so the engine never has to."""
    name: str
    hp: int
    attack_damage: int
    defense: int
    position: tuple[int, int]


@dataclass
class LevelData:
    """In-memory view of a room. Pure runtime data with no DB awareness —
    WorldState is constructed from this. (Replaces the old hardcoded
    config.LevelConfig.)"""
    width: int
    height: int
    spawn_points: list[tuple[int, int]]
    walls: set[tuple[int, int]]
    enemies: list[EnemySpawn] = field(default_factory=list)
    capacity: int = 0


async def load_level(session: AsyncSession, room_id: int) -> LevelData:
    """Read a room (+ resolve its enemy stats) into a ready-to-play LevelData."""
    room = await session.get(Room, room_id)
    if room is None:
        raise ValueError(f"no room with id {room_id}")

    # Terrain (ASCII grid) -> the set of blocking cells, via the tile vocabulary.
    walls = {
        (x, y)
        for y, row in enumerate(room.terrain)
        for x, ch in enumerate(row)
        if not TileType(ch).passable
    }

    enemies: list[EnemySpawn] = []
    for spawn in room.enemy_spawns:
        ed = await session.get(EnemyDef, spawn["enemy_id"])
        if ed is None:
            raise ValueError(f"room '{room.name}' references unknown enemy_id {spawn['enemy_id']}")
        enemies.append(EnemySpawn(
            name=ed.name, hp=ed.hp, attack_damage=ed.attack_damage,
            defense=ed.defense, position=(spawn["x"], spawn["y"]),
        ))

    return LevelData(
        width=room.width,
        height=room.height,
        spawn_points=[tuple(p) for p in room.spawn_points],
        walls=walls,
        enemies=enemies,
        capacity=room.capacity,
    )
