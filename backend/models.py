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
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.config import HUNGER_MAX, PLAYER_MAX_HP, PLAYER_STARTING_COINS
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
    # Authored identity, stable across database rebuilds and row-id changes.
    # Nullable only so an old/local row with a malformed persona can still be
    # inspected and repaired; every validated seed and living-world record
    # uses this value instead of the numeric row id.
    content_id: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True, index=True,
    )
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
    # A scalar balance, deliberately separate from the slot-limited pack.
    coins: Mapped[int] = mapped_column(Integer, default=PLAYER_STARTING_COINS)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ShopState(Base):
    """Daily lifecycle state for one authored shop."""
    __tablename__ = "shop_states"

    shop_id: Mapped[str] = mapped_column(String, primary_key=True)
    # This row remains after sell-out so opening an empty shop cannot restock it.
    last_restock_on: Mapped[date] = mapped_column(Date)


class ShopStock(Base):
    """One globally available item copy in one shop slot."""
    __tablename__ = "shop_stock"

    shop_id: Mapped[str] = mapped_column(String, primary_key=True)
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Same immutable item_view snapshot used by packs and chests.
    item: Mapped[dict] = mapped_column(JSON)
    price: Mapped[int] = mapped_column(Integer)
    minted: Mapped[bool] = mapped_column(default=False)
    # Also acts as the optimistic token for a client holding yesterday's panel.
    stocked_on: Mapped[date] = mapped_column(Date)


class NoticePost(Base):
    """One globally visible, expiring player message on an authored board."""
    __tablename__ = "notice_posts"
    __table_args__ = (
        UniqueConstraint(
            "board_id", "author_player_id",
            name="uq_notice_post_board_author",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[str] = mapped_column(String, index=True)
    author_player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    # Snapshot the public name so rendering never needs an account-table join.
    author_name: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


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


# --- Living world persistence -------------------------------------------------
#
# These tables deliberately contain no player quest state. People pursue
# their own goals and leave events, memories, relationships, and facts behind;
# players encounter those consequences naturally rather than accepting a
# checklist owned by the game.


class WorldState(Base):
    """Singleton clock and global deterministic simulation state.

    `world_minute` is the canonical time unit. Keeping it integral makes
    catch-up, replay, schedule anchors, and the 3-6 daily deliberation cadence
    deterministic. `last_real_at` is only the bridge used to decide how much
    simulated time may be caught up after a restart.
    """
    __tablename__ = "world_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_world_state_singleton"),
        CheckConstraint("world_minute >= 0", name="ck_world_state_minute_nonnegative"),
        CheckConstraint("revision >= 0", name="ck_world_state_revision_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    world_seed: Mapped[int] = mapped_column(Integer, default=1)
    world_minute: Mapped[int] = mapped_column(Integer, default=0)
    last_real_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    variables: Mapped[dict] = mapped_column(JSON, default=dict)


class WorldEvent(Base):
    """Append-only causal chronicle entry produced by deterministic rules."""
    __tablename__ = "world_events"
    __table_args__ = (
        CheckConstraint("world_minute >= 0", name="ck_world_event_minute_nonnegative"),
        Index("ix_world_events_minute_id", "world_minute", "id"),
        Index("ix_world_events_kind_minute", "kind", "world_minute"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Optional idempotency key for effects that may be retried. Multiple
    # ordinary observations of the same kind intentionally leave distinct
    # rows, so callers opt in to deduplication.
    dedupe_key: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True,
    )
    kind: Mapped[str] = mapped_column(String, index=True)
    world_minute: Mapped[int] = mapped_column(Integer, index=True)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id"), nullable=True, index=True,
    )
    summary: Mapped[str] = mapped_column(Text)
    # The engine decides who may see an event. Witness ids and private rule
    # data stay structured instead of being smuggled into prose.
    visibility: Mapped[str] = mapped_column(String, default="witnessed")
    witnesses: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )


class ScheduledWorldEvent(Base):
    """One durable future action, ordered without per-NPC async timers."""
    __tablename__ = "scheduled_world_events"
    __table_args__ = (
        CheckConstraint("due_minute >= 0", name="ck_scheduled_event_due_nonnegative"),
        CheckConstraint("attempt_count >= 0", name="ck_scheduled_event_attempts_nonnegative"),
        CheckConstraint(
            "status IN ('pending', 'running', 'resolved', 'cancelled', 'failed')",
            name="ck_scheduled_event_status",
        ),
        Index(
            "ix_scheduled_events_dispatch",
            "status", "due_minute", "priority", "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Required so replay, retries, and dialogue cascades cannot resolve the
    # same consequential action twice.
    dedupe_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    due_minute: Mapped[int] = mapped_column(Integer, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id"), nullable=True, index=True,
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    resolved_at_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)


class NPCMemory(Base):
    """One durable observation, rumour, promise, reflection, or outcome."""
    __tablename__ = "npc_memories"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('observation', 'conversation', 'rumour', 'reflection', "
            "'promise', 'plan', 'outcome')",
            name="ck_npc_memory_kind",
        ),
        CheckConstraint(
            "importance >= 0.0 AND importance <= 10.0",
            name="ck_npc_memory_importance",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_npc_memory_confidence",
        ),
        CheckConstraint("world_minute >= 0", name="ck_npc_memory_minute_nonnegative"),
        CheckConstraint(
            "secrecy >= 0.0 AND secrecy <= 1.0",
            name="ck_npc_memory_secrecy",
        ),
        CheckConstraint(
            "cascade_depth >= 0",
            name="ck_npc_memory_cascade_depth_nonnegative",
        ),
        Index("ix_npc_memories_owner_minute", "npc_content_id", "world_minute", "id"),
        Index("ix_npc_memories_owner_kind", "npc_content_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stable event-derived identity used by rumour source chains and replay.
    memory_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    npc_content_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    subject_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    object_id: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # Ordered carrier chain, e.g. ["hester-vale", "fen-darrow"]. It is the
    # provenance proof that prevents global-knowledge dialogue.
    source_chain: Mapped[list] = mapped_column(JSON, default=list)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("npc_memories.memory_key"), nullable=True,
    )
    source_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("world_events.id"), nullable=True, index=True,
    )
    importance: Mapped[float] = mapped_column(Float, default=2.5)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    shareable: Mapped[bool] = mapped_column(default=True)
    secrecy: Mapped[float] = mapped_column(Float, default=0.0)
    cascade_depth: Mapped[int] = mapped_column(Integer, default=0)
    world_minute: Mapped[int] = mapped_column(Integer, index=True)
    last_recalled_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supersedes_memory_id: Mapped[int | None] = mapped_column(
        ForeignKey("npc_memories.id"), nullable=True,
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class NPCRelationship(Base):
    """Directional, multi-axis relationship from an NPC to a person."""
    __tablename__ = "npc_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_npc_content_id", "target_kind", "target_id",
            name="uq_npc_relationship_direction",
        ),
        CheckConstraint(
            "target_kind IN ('npc', 'player')",
            name="ck_npc_relationship_target_kind",
        ),
        CheckConstraint(
            "target_kind != 'npc' OR source_npc_content_id != target_id",
            name="ck_npc_relationship_not_self",
        ),
        CheckConstraint(
            "affinity BETWEEN -100 AND 100 AND "
            "trust BETWEEN -100 AND 100 AND "
            "fear BETWEEN -100 AND 100 AND "
            "respect BETWEEN -100 AND 100 AND "
            "obligation BETWEEN -100 AND 100 AND "
            "intimacy BETWEEN -100 AND 100 AND "
            "grievance BETWEEN -100 AND 100",
            name="ck_npc_relationship_axes_bounded",
        ),
        CheckConstraint(
            "familiarity BETWEEN 0 AND 100",
            name="ck_npc_relationship_familiarity_bounded",
        ),
        Index("ix_npc_relationships_target", "target_kind", "target_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_npc_content_id: Mapped[str] = mapped_column(String, index=True)
    target_kind: Mapped[str] = mapped_column(String)
    target_id: Mapped[str] = mapped_column(String)
    affinity: Mapped[float] = mapped_column(Float, default=0.0)
    trust: Mapped[float] = mapped_column(Float, default=0.0)
    fear: Mapped[float] = mapped_column(Float, default=0.0)
    respect: Mapped[float] = mapped_column(Float, default=0.0)
    obligation: Mapped[float] = mapped_column(Float, default=0.0)
    intimacy: Mapped[float] = mapped_column(Float, default=0.0)
    grievance: Mapped[float] = mapped_column(Float, default=0.0)
    familiarity: Mapped[float] = mapped_column(Float, default=0.0)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    last_interaction_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at_minute: Mapped[int] = mapped_column(Integer, default=0)


class NPCGoal(Base):
    """A person's private intention and current closed-vocabulary plan.

    This is not a quest record. It exists whether or not any player learns
    about it, and `next_deliberation_minute` lets the simulator reconsider a
    person only a few times per day while continuous actions run separately.
    """
    __tablename__ = "npc_goals"
    __table_args__ = (
        UniqueConstraint(
            "npc_content_id", "goal_key",
            name="uq_npc_goal_owner_key",
        ),
        CheckConstraint(
            "status IN ('dormant', 'active', 'blocked', 'completed', "
            "'failed', 'abandoned')",
            name="ck_npc_goal_status",
        ),
        CheckConstraint(
            "priority BETWEEN 0 AND 100 AND urgency BETWEEN 0 AND 100",
            name="ck_npc_goal_weights_bounded",
        ),
        CheckConstraint(
            "progress >= 0.0 AND progress <= 1.0",
            name="ck_npc_goal_progress",
        ),
        CheckConstraint(
            "created_at_minute >= 0 AND next_deliberation_minute >= 0",
            name="ck_npc_goal_minutes_nonnegative",
        ),
        Index(
            "ix_npc_goals_deliberation",
            "status", "next_deliberation_minute", "npc_content_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    npc_content_id: Mapped[str] = mapped_column(String, index=True)
    goal_key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, index=True)
    origin: Mapped[str] = mapped_column(String, default="authored")
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[float] = mapped_column(Float, default=50.0)
    urgency: Mapped[float] = mapped_column(Float, default=0.0)
    preconditions: Mapped[list] = mapped_column(JSON, default=list)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    deadline_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="dormant", index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_steps: Mapped[list] = mapped_column(JSON, default=list)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    created_at_minute: Mapped[int] = mapped_column(Integer, default=0)
    last_deliberated_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_deliberation_minute: Mapped[int] = mapped_column(Integer, default=0, index=True)
    context: Mapped[dict] = mapped_column(JSON, default=dict)


class TriggerFiring(Base):
    """Auditable record that an authored condition fired exactly once."""
    __tablename__ = "trigger_firings"
    __table_args__ = (
        CheckConstraint("fired_at_minute >= 0", name="ck_trigger_firing_minute_nonnegative"),
        CheckConstraint("ordinal >= 1", name="ck_trigger_firing_ordinal_positive"),
        Index(
            "ix_trigger_firings_scope_time",
            "trigger_id", "scope_id", "fired_at_minute",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger_id: Mapped[str] = mapped_column(String, index=True)
    scope_id: Mapped[str] = mapped_column(String, index=True)
    actor_npc_content_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=1)
    fired_at_minute: Mapped[int] = mapped_column(Integer, index=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("world_events.id"), nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String, default="applied")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class WorldFact(Base):
    """Structured world truth; knowledge of it still travels through memory."""
    __tablename__ = "world_facts"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_world_fact_confidence",
        ),
        CheckConstraint(
            "established_at_minute >= 0 AND updated_at_minute >= 0",
            name="ck_world_fact_minutes_nonnegative",
        ),
        Index("ix_world_facts_subject_predicate", "subject_id", "predicate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    subject_id: Mapped[str] = mapped_column(String, index=True)
    predicate: Mapped[str] = mapped_column(String, index=True)
    object_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    # Hidden is the default: facts become player-facing only through a memory,
    # witnessed event, dialogue, or another explicit discovery rule.
    visibility: Mapped[str] = mapped_column(String, default="hidden")
    established_at_minute: Mapped[int] = mapped_column(Integer, default=0)
    updated_at_minute: Mapped[int] = mapped_column(Integer, default=0)
    expires_at_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("world_events.id"), nullable=True,
    )


class FrontierNode(Base):
    """Persistent provenance for one generated or discovered world room.

    The Room row stores the playable tiles; this row stores why that room
    exists and enough deterministic input to reproduce it. An authored-region
    discovery uses the same graph node but sets `authored_region_id`, allowing
    wilderness generation to yield to a hand-built kingdom without pretending
    the transition was a quest reward.
    """
    __tablename__ = "frontier_nodes"
    __table_args__ = (
        CheckConstraint("depth >= 0", name="ck_frontier_node_depth_nonnegative"),
        CheckConstraint(
            "discovered_at_minute >= 0",
            name="ck_frontier_node_discovery_minute_nonnegative",
        ),
        Index("ix_frontier_nodes_biome_depth", "biome", "depth"),
        Index(
            "ix_frontier_nodes_authored_region",
            "authored_region_id", "depth",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stable graph identity derived by the frontier service from the parent
    # edge and world seed; unlike rooms.id it survives export/re-import.
    node_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id"), unique=True, index=True,
    )
    world_seed: Mapped[int] = mapped_column(Integer, index=True)
    generation_seed: Mapped[int] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer, default=0, index=True)
    biome: Mapped[str] = mapped_column(String, index=True)
    generator_kind: Mapped[str] = mapped_column(String)
    generator_version: Mapped[str] = mapped_column(String, default="1")
    generator_params: Mapped[dict] = mapped_column(JSON, default=dict)
    # Non-tile content discovered here: landmarks, rumours, encounter hints,
    # and other validated generator output. Playable room geometry remains
    # normalized in rooms rather than duplicated.
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    generation_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    authored_region_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True,
    )
    discovered_at_minute: Mapped[int] = mapped_column(Integer, default=0)


class FrontierExit(Base):
    """One durable edge whose rising pressure can reveal more of the world."""
    __tablename__ = "frontier_exits"
    __table_args__ = (
        UniqueConstraint(
            "source_room_id", "source_x", "source_y",
            name="uq_frontier_exit_source_tile",
        ),
        CheckConstraint(
            "status IN ('sealed', 'frontier', 'connected')",
            name="ck_frontier_exit_status",
        ),
        CheckConstraint(
            "(status = 'connected' AND target_room_id IS NOT NULL) OR "
            "(status IN ('sealed', 'frontier') AND target_room_id IS NULL)",
            name="ck_frontier_exit_target_matches_status",
        ),
        CheckConstraint(
            "discovery_pressure >= 0.0",
            name="ck_frontier_exit_pressure_nonnegative",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_frontier_exit_attempts_nonnegative",
        ),
        CheckConstraint(
            "created_at_minute >= 0",
            name="ck_frontier_exit_created_minute_nonnegative",
        ),
        Index(
            "ix_frontier_exits_expandable",
            "status", "discovery_pressure", "source_room_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id"), index=True,
    )
    source_x: Mapped[int] = mapped_column(Integer)
    source_y: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="frontier", index=True)
    target_room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id"), nullable=True, index=True,
    )
    # Rising-luck accumulator. It intentionally has no upper bound: the
    # deterministic discovery policy may consume/reset it at its own threshold
    # and very unlucky paths continue becoming more likely rather than capping.
    discovery_pressure: Mapped[float] = mapped_column(Float, default=0.0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    # Each attempt derives its roll from this stable seed + attempt_count.
    roll_seed: Mapped[int] = mapped_column(Integer)
    biome_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    generator_hint: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at_minute: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
