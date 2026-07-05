"""Factory/mapper: a stored `Room` row -> a runtime `LevelData`.

This is the seam between the persistence model (ORM `Room`/`EnemyDef`, which
know about the DB) and the domain model (`LevelData`/`WorldState`, which know
about gameplay and nothing about the DB). It is the ONLY place that reads the
DB to build a world — combat then runs purely from memory (BACKEND.md: DB at
the edges, never in the hot loop).
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EnemyDef, ObjectType, Room, RoomConnection, TileType


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
class RoomObject:
    """A client-safe view of an object placed in a room."""
    id: str
    type: str
    position: tuple[int, int]
    label: str
    description: str
    details: list[str] = field(default_factory=list)

    def to_summary_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "position": [self.position[0], self.position[1]],
            "label": self.label,
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "position": [self.position[0], self.position[1]],
            "label": self.label,
            "description": self.description,
            "details": list(self.details),
        }


@dataclass
class LevelData:
    """In-memory view of a room. Pure runtime data with no DB awareness —
    WorldState is constructed from this. (Replaces the old hardcoded
    config.LevelConfig.)"""
    room_id: int
    room_name: str
    width: int
    height: int
    spawn_points: list[tuple[int, int]]
    walls: set[tuple[int, int]]
    enemies: list[EnemySpawn] = field(default_factory=list)
    objects: list[RoomObject] = field(default_factory=list)
    capacity: int = 0
    # Door/portal tile -> destination room id. Loaded once with the room so
    # the engine can answer "does this tile lead somewhere?" without the DB.
    connections: dict[tuple[int, int], int] = field(default_factory=dict)


def _object_payload(raw: dict, index: int) -> RoomObject:
    object_type = ObjectType(raw["type"])
    label = {
        ObjectType.CHEST: "Chest",
        ObjectType.FIRE_BARREL: "Fire Barrel",
    }[object_type]
    description = {
        ObjectType.CHEST: "An old iron-bound chest with a stubborn latch.",
        ObjectType.FIRE_BARREL: "An oil-soaked barrel with blackened bands.",
    }[object_type]
    details = {
        ObjectType.CHEST: ["Latch: rusted", "Contents: sealed"],
        ObjectType.FIRE_BARREL: ["Stability: fragile", "Surface: oily"],
    }[object_type]

    return RoomObject(
        id=f"object_{index + 1}",
        type=object_type.value,
        position=(raw["x"], raw["y"]),
        label=label,
        description=description,
        details=details,
    )


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

    objects = [
        _object_payload(raw, i)
        for i, raw in enumerate(room.objects or [])
    ]

    connection_rows = (await session.execute(
        select(RoomConnection).where(RoomConnection.from_room_id == room_id)
    )).scalars().all()
    connections = {(c.from_x, c.from_y): c.to_room_id for c in connection_rows}

    return LevelData(
        room_id=room.id,
        room_name=room.name,
        width=room.width,
        height=room.height,
        spawn_points=[tuple(p) for p in room.spawn_points],
        walls=walls,
        enemies=enemies,
        objects=objects,
        capacity=room.capacity,
        connections=connections,
    )
