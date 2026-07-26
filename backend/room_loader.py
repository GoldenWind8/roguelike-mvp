"""Factory/mapper: a stored `Room` row -> a runtime `RoomTemplate`.

This is the seam between the persistence model (ORM `Room`/`EnemyDef`, which
know about the DB) and the domain model (`RoomTemplate`/`RoomState`, which know
about gameplay and nothing about the DB). It is the ONLY place that reads the
DB to build a world — combat then runs purely from memory (BACKEND.md: DB at
the edges, never in the hot loop).
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.actor_defs import enemy_art
from backend.models import EnemyDef, Room, RoomConnection, TileType
from backend.object_defs import get_object_definition
from backend.object_store import apply_object_states


@dataclass
class EnemySpawn:
    """A fully-resolved enemy ready to place — stats already looked up from the
    enemy_defs table, so the engine never has to."""
    name: str
    hp: int
    attack_damage: int
    defense: int
    position: tuple[int, int]
    image: str | None = None
    visual_size: tuple[int, int] = (1, 1)


@dataclass(frozen=True)
class RoomExit:
    """Client-facing presentation for one connected door or portal.

    Traversal continues to use ``RoomTemplate.connections`` as its compact
    authoritative lookup.  This parallel view only gives the renderer a safe
    destination label so an exit can advertise where it leads.
    """
    position: tuple[int, int]
    to_room_id: int
    label: str

    def to_dict(self) -> dict:
        return {
            "position": [self.position[0], self.position[1]],
            "to_room_id": self.to_room_id,
            "label": self.label,
        }


@dataclass
class RoomObject:
    """A client-safe view of an object placed in a room.

    Chest lifecycle state lives here as LIVE state, not design data: `opened`
    flips when the first player opens it (contents are rolled at that moment,
    docs/LOOT.md), and `contents` holds rolled item_views nobody could carry
    yet (opener's pack was full) — anyone may claim them later. Unlike
    fungible enemies, this state PERSISTS: every open/take writes through to
    `object_instances` (object_store.py) and load_room overlays it back, so
    a looted chest stays looted across evictions and restarts."""
    id: str
    type: str
    position: tuple[int, int]
    label: str
    description: str
    details: list[str] = field(default_factory=list)
    footprint: tuple[tuple[int, int], ...] = ((0, 0),)
    blocks_movement: bool = True
    image: str | None = None
    visual_size: tuple[int, int] = (1, 1)
    opened: bool = False
    contents: list = field(default_factory=list)
    interaction: str | None = None

    def occupied_cells(self) -> tuple[tuple[int, int], ...]:
        x, y = self.position
        return tuple((x + dx, y + dy) for dx, dy in self.footprint)

    def distance_from(self, x: int, y: int) -> int:
        """Shortest Manhattan distance to any cell in this object's body."""
        return min(abs(x - ox) + abs(y - oy) for ox, oy in self.occupied_cells())

    def to_summary_dict(self) -> dict:
        summary = {
            "id": self.id,
            "type": self.type,
            "position": [self.position[0], self.position[1]],
            "label": self.label,
            # These are expanded by the server. The client uses them for hit
            # testing and presentation, never to decide whether movement is
            # legal.
            "occupied_cells": [[x, y] for x, y in self.occupied_cells()],
            "blocks_movement": self.blocks_movement,
            "image": self.image,
            "visual_size": [self.visual_size[0], self.visual_size[1]],
            "opened": self.opened,
            # A count, not the items — walking past a chest tells you THAT
            # something waits inside, inspecting tells you what.
            "contents_count": len(self.contents),
        }
        if self.interaction is not None:
            summary["interaction"] = self.interaction
        return summary

    def to_dict(self) -> dict:
        return {
            **self.to_summary_dict(),
            "description": self.description,
            "details": list(self.details),
            "contents": list(self.contents),
        }


@dataclass
class RoomTemplate:
    """In-memory view of a room. Pure runtime data with no DB awareness —
    RoomState is constructed from this. (Replaces the old hardcoded
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
    # The same exits with client-safe destination names. Presentation only;
    # movement never trusts or consults these labels.
    exits: list[RoomExit] = field(default_factory=list)
    # Note: no `mode` field. A room's timing model is DERIVED live from who is
    # present (modes.derive_mode, M7) — a template with enemies wakes up combat
    # because those enemies are hostile, not because a stored flag says so.


def _object_payload(raw: dict, index: int) -> RoomObject:
    definition = get_object_definition(raw["type"])
    if definition is None:
        raise ValueError(f"unknown object type '{raw['type']}'")

    return RoomObject(
        # Authored rooms use stable placement ids so reordering their JSON does
        # not attach persisted chest/object state to the wrong object. Generated
        # and legacy rooms retain the deterministic index fallback.
        id=raw.get("id", f"object_{index + 1}"),
        type=definition.id,
        position=(raw["x"], raw["y"]),
        label=definition.label,
        description=definition.description,
        details=list(definition.details),
        footprint=definition.footprint,
        blocks_movement=definition.blocks_movement,
        image=definition.image,
        visual_size=definition.visual_size,
        interaction=definition.interaction,
    )


async def load_room(session: AsyncSession, room_id: int) -> RoomTemplate:
    """Read a room (+ resolve its enemy stats) into a ready-to-play RoomTemplate."""
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
        art = enemy_art(ed.name)
        enemies.append(EnemySpawn(
            name=ed.name, hp=ed.hp, attack_damage=ed.attack_damage,
            defense=ed.defense, position=(spawn["x"], spawn["y"]),
            image=art.image if art else None,
            visual_size=art.visual_size if art else (1, 1),
        ))

    objects = [
        _object_payload(raw, i)
        for i, raw in enumerate(room.objects or [])
    ]
    # Overlay what play did to these objects (opened chests keep their state
    # across evictions — object_store.py).
    await apply_object_states(session, room_id, objects)

    connection_rows = (await session.execute(
        select(RoomConnection)
        .where(RoomConnection.from_room_id == room_id)
        .order_by(RoomConnection.id)
    )).scalars().all()
    connections = {(c.from_x, c.from_y): c.to_room_id for c in connection_rows}
    destination_ids = {c.to_room_id for c in connection_rows}
    destination_names = {}
    if destination_ids:
        destination_rooms = (await session.execute(
            select(Room).where(Room.id.in_(destination_ids))
        )).scalars().all()
        destination_names = {destination.id: destination.name for destination in destination_rooms}
    exits = [
        RoomExit(
            position=(connection.from_x, connection.from_y),
            to_room_id=connection.to_room_id,
            label=destination_names.get(connection.to_room_id, "Unknown road"),
        )
        for connection in connection_rows
    ]

    return RoomTemplate(
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
        exits=exits,
    )
