"""ORM models + the closed tile/object vocabularies.

Two layers of the world (see ARCHITECTURE.md "separate terrain from entities"):
  - terrain : a dense char grid of a SMALL closed set of TileTypes (floor/wall/
              door/portal). LLM-friendly — a model "draws" the map as ASCII.
  - objects : a sparse list of stateful, interactive things. Trusted collision
              and art metadata live in object_defs.py; room JSON stores only a
              definition id and placement.

Enemies are NORMALIZED: their stats live once in `enemy_defs`; a room only
stores a placement {enemy_id, x, y} and loads the rest by id.
"""
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.config import HUNGER_MAX, PLAYER_MAX_HP
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
    """The deliberately small object subset used by current room generators.

    The full extensible catalogue lives in object_defs.py. Keeping generated
    rooms on this one-tile subset is a generation policy, not a runtime branch.
    """
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


class ItemDef(Base):
    """One loot item the world can dispense — the GLOBAL ITEM POOL
    (docs/LOOT.md). Rows are immutable once minted: held copies are
    denormalized snapshots (items.item_view), so editing a row would silently
    fork it from every copy in a player's pack. New power enters as new rows.

    Two provenances share the table on purpose: hand-authored seeds
    (origin="seed") and premium-LLM inventions minted at chest-open time
    (origin="llm"). Both pass items.validate_item before insert — the DB
    can't check a JSON payload, so the gate lives in code, same as rooms.
    """
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    rarity: Mapped[str] = mapped_column(String, index=True)   # items.Rarity
    # "type" is a soft keyword everywhere — the column says what it holds.
    item_type: Mapped[str] = mapped_column(String)            # items.ItemType
    art: Mapped[dict] = mapped_column(JSON)                   # {"kind": "emoji"|"url", "value": ...}
    payload: Mapped[dict] = mapped_column(JSON)               # validated effect data
    origin: Mapped[str] = mapped_column(String, default="seed")  # "seed" | "llm"
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


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
    # The player this NPC follows (NPCS.md "Followers"), or NULL. Since M8 it
    # holds a `players.id` — stable across sessions, so a returning login
    # rebinds to its follower automatically. Still a plain String, not a FK:
    # promotion waits one more milestone, until eviction ordering provably
    # never saves an NPC before its owner exists (ACCOUNTS.md). Persists
    # across room resets and restarts like the rest of the row.
    party_owner_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class PlayerRow(Base):
    """An account and its one character — the row IS the character
    (ACCOUNTS.md Decision 1). `id` is the identity everything else references
    (Decision 2): an opaque "player_<uuid>" string, prefixed so
    RoomState.get_entity's prefix dispatch keeps working and stable so
    `npcs.party_owner_id` finally points at something real. Usernames are for
    humans and login only — renameable later without breaking references.

    Two column groups with different lifecycles:
      - auth (username / password_hash / email): written by /register, read by
        /login, never sent to clients. email is nullable, unverified, unused
        until the password-reset trigger fires (Decision 4).
      - game state (room_id, x, y, hp): written at the edges only — disconnect
        and shutdown — the `npcs` rhythm (Decision 7). All nullable-or-defaulted
        because a fresh account has no position yet; NULL room means "spawn at
        the default room", which is also the fallback whenever a saved location
        stops making sense (deleted room, dead character).
    """
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), nullable=True, default=None)
    x: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    y: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    hp: Mapped[int] = mapped_column(Integer, default=PLAYER_MAX_HP)
    # The 10-slot pack, saved at the same edges as position/hp: a list of
    # {"item": <items.item_view dict>, "quantity": int, "equipped": bool}.
    # Denormalized snapshots on purpose — restoring a pack never queries the
    # items table, and a later change to a pool row can't mutate a held copy.
    inventory: Mapped[list] = mapped_column(JSON, default=list)
    # The hunger meter, saved at the same edges as hp. Stored as the raw
    # float (the ticker drains fractions); a fresh account starts full.
    hunger: Mapped[float] = mapped_column(default=float(HUNGER_MAX))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ObjectInstance(Base):
    """Play-mutated object state layered over `Room.objects` design data —
    the object half of the `npcs` pattern: rooms stay templates, this row is
    what play did to one object in one room. Today that means chest
    lifecycle: `opened` plus the rolled `contents` nobody has carried off
    yet, so a looted chest stays looted across room evictions and restarts
    (no re-arming by room-cycling). A row exists only once play first
    touches the object; loaded and saved by `object_store.py`."""
    __tablename__ = "object_instances"

    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), primary_key=True)
    # The runtime object id ("object_3"), derived from the object's index in
    # Room.objects — stable as long as the design list is never reordered.
    object_id: Mapped[str] = mapped_column(String, primary_key=True)
    opened: Mapped[bool] = mapped_column(default=False)
    contents: Mapped[list] = mapped_column(JSON, default=list)  # item_views awaiting takers


class Room(Base):
    """A level as data. `terrain`/`objects`/`spawn_points`/`enemy_spawns` are
    JSON because their shape varies — which means *we* validate them on the way
    in (the DB won't); see room_validation.validate_room."""
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable id for a version-controlled authored room. Generated rooms leave
    # this NULL and are owned by the database after validation.
    content_id: Mapped[str | None] = mapped_column(String, nullable=True, unique=True, index=True)
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
