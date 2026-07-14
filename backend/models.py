"""ORM models + the closed tile/object vocabularies.

Two layers of the world (see ARCHITECTURE.md "separate terrain from entities"):
  - terrain : a dense char grid of a SMALL closed set of TileTypes (floor/wall/
              door/portal). LLM-friendly — a model "draws" the map as ASCII.
  - objects : a sparse list of stateful, interactive things (chests, barrels).
              The OPEN content edge — new object types add an ObjectType + a
              handler later, they never widen the terrain vocabulary.

Enemies are NORMALIZED: their stats live once in `enemy_defs`; a room only
stores a placement {enemy_id, x, y} and loads the rest by id.
"""
from enum import Enum

from sqlalchemy import ForeignKey, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base


class TileType(str, Enum):
    """Closed terrain vocabulary. The enum *value is the map character*, so the
    stored `terrain` is a human- and LLM-readable ASCII grid."""
    FLOOR = "."
    WALL = "#"
    DOOR = "+"
    PORTAL = "O"

    @property
    def passable(self) -> bool:
        """Can an entity stand on / walk over this tile? Walls block; floor and
        the two passage types do not. #21's is_valid_position uses this."""
        return self is not TileType.WALL


class ObjectType(str, Enum):
    """Interactive objects placed on top of terrain. Extensible: a new object
    is a new member here + (later) a handler — terrain stays untouched."""
    CHEST = "chest"
    FIRE_BARREL = "fire_barrel"


class EnemyDef(Base):
    """Reusable enemy definition. Stats are written ONCE here and referenced by
    id from any number of rooms — the room JSON never duplicates them.

    on_spawn / on_death are effect-data hooks (drawn from the closed effect
    vocabulary, validated + executed by the effects system in #22/M0): the
    flexibility seam for "drop loot on death", "explode", "summon on spawn".
    """
    __tablename__ = "enemy_defs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    hp: Mapped[int] = mapped_column(Integer)
    attack_damage: Mapped[int] = mapped_column(Integer)
    defense: Mapped[int] = mapped_column(Integer)
    on_spawn: Mapped[list] = mapped_column(JSON, default=list)   # list[effect]
    on_death: Mapped[list] = mapped_column(JSON, default=list)   # list[effect]


class NPCRow(Base):
    """An individual (NPCS.md Decision 9): one row per NPC that exists in the
    world. `room_id` is where the NPC *is*, not room design data — rooms never
    list NPCs, so room load has two occupant sources: design spawns (fungible,
    reseeded) and these rows (individuals, whose state survives).

    Play EDITS these rows (hp, position, disposition) — unlike the template
    tables above, which play never mutates. `persona` is validated against the
    persona schema before insert/load (persona.validate_persona); `memory` is
    the bounded dialogue transcript, per-instance state like everything else
    here. party_owner_id arrives with the party-effects slice, not before.
    """
    __tablename__ = "npcs"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))
    name: Mapped[str] = mapped_column(String)
    x: Mapped[int] = mapped_column(Integer)
    y: Mapped[int] = mapped_column(Integer)
    hp: Mapped[int] = mapped_column(Integer)
    max_hp: Mapped[int] = mapped_column(Integer)
    defense: Mapped[int] = mapped_column(Integer, default=0)
    attack_damage: Mapped[int] = mapped_column(Integer, default=0)
    is_alive: Mapped[bool] = mapped_column(default=True)
    disposition: Mapped[str] = mapped_column(String, default="neutral")
    persona: Mapped[dict] = mapped_column(JSON, default=dict)
    memory: Mapped[list] = mapped_column(JSON, default=list)   # bounded transcript


class Room(Base):
    """A level as data. `terrain`/`objects`/`spawn_points`/`enemy_spawns` are
    JSON because their shape varies — which means *we* validate them on the way
    in (the DB won't); see room_validation.validate_room."""
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)

    terrain: Mapped[list] = mapped_column(JSON)              # list[str] — the ASCII grid
    objects: Mapped[list] = mapped_column(JSON, default=list)        # list[{type,x,y,...}]
    spawn_points: Mapped[list] = mapped_column(JSON)         # list[[x, y]] — around the entry
    enemy_spawns: Mapped[list] = mapped_column(JSON, default=list)   # list[{enemy_id,x,y}]

    @property
    def capacity(self) -> int:
        """Max players this room holds = number of spawn points. The engine
        (#21) uses this in join() in place of the old global MAX_PLAYERS."""
        return len(self.spawn_points)


class RoomConnection(Base):
    """A directed edge in the world graph: a door/portal tile in `from_room`
    leads to `to_room`. Modeled as a plain adjacency-list row (the graph is
    small and read-mostly) — the FK lives in a real column, never in JSON.

    Traversal is live: load_room reads these into RoomTemplate.connections and
    stepping onto (from_x, from_y) transfers the player to to_room."""
    __tablename__ = "room_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))
    to_room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))
    # Which tile in from_room is the door/portal you step on.
    from_x: Mapped[int] = mapped_column(Integer)
    from_y: Mapped[int] = mapped_column(Integer)
