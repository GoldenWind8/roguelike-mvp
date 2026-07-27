"""Database operations for the deterministic living-world simulator.

The service layer decides *why* somebody acts.  This module is deliberately
boring: stable identities, idempotent inserts, ordered queue reads, and small
state transitions.  Keeping persistence here makes it possible to replay and
test the simulator without involving FastAPI, websockets, or a language model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.living_world.memory import Memory, retrieve_memories
from backend.living_world.movement import RouteEdge
from backend.living_world.relationships import (
    Relationship,
    apply_relationship_delta,
)
from backend.models import (
    NPCGoal,
    NPCMemory,
    NPCRelationship,
    NPCRow,
    Room,
    RoomConnection,
    ScheduledWorldEvent,
    WorldEvent,
    WorldFact,
    WorldState,
)


# Authored social locations can share one playable grid room until that
# district earns its own map. The simulation still reasons in stable location
# ids; this adapter resolves them to present geometry without inventing
# disconnected placeholder rooms.
_LOCATION_ROOM_ALIASES = {
    "alderwick": "oakrun_crossroads",
    "briarwash_fields": "oakrun_orchard_lane",
    "hollowmere_post": "oakrun_fieldsite_verge",
    "saint-oree-hospice": "bellifont",
}


def epoch_datetime(value: float) -> datetime:
    """Convert a wall-clock epoch to the UTC type persisted by WorldState."""
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def datetime_epoch(value: datetime | float | int) -> float:
    """Read old numeric prototypes and current timezone-aware rows alike."""
    if isinstance(value, (float, int)):
        return float(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


async def get_or_create_world_state(
    session: AsyncSession,
    *,
    wall_now: float,
    world_seed: int,
) -> tuple[WorldState, bool]:
    row = await session.get(WorldState, 1)
    if row is not None:
        return row, False
    row = WorldState(
        id=1,
        world_seed=world_seed,
        world_minute=0,
        last_real_at=epoch_datetime(wall_now),
        revision=0,
        variables={},
    )
    session.add(row)
    await session.flush()
    return row, True


async def schedule_once(
    session: AsyncSession,
    *,
    dedupe_key: str,
    kind: str,
    due_minute: int,
    priority: int = 0,
    actor_id: str | None = None,
    target_id: str | None = None,
    room_id: int | None = None,
    payload: Mapping[str, object] | None = None,
) -> tuple[ScheduledWorldEvent, bool]:
    """Insert a future action once, returning an existing retry if present."""
    existing = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.dedupe_key == dedupe_key
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = ScheduledWorldEvent(
        dedupe_key=dedupe_key,
        kind=kind,
        due_minute=due_minute,
        priority=priority,
        actor_id=actor_id,
        target_id=target_id,
        room_id=room_id,
        payload=dict(payload or {}),
    )
    session.add(row)
    await session.flush()
    return row, True


async def next_due_event(
    session: AsyncSession,
    *,
    through_minute: int,
    excluded_ids: Iterable[int] = (),
) -> ScheduledWorldEvent | None:
    excluded = tuple(excluded_ids)
    statement = select(ScheduledWorldEvent).where(
        ScheduledWorldEvent.status == "pending",
        ScheduledWorldEvent.due_minute <= through_minute,
    )
    if excluded:
        statement = statement.where(ScheduledWorldEvent.id.not_in(excluded))
    return (await session.execute(
        statement.order_by(
            ScheduledWorldEvent.due_minute,
            ScheduledWorldEvent.priority,
            ScheduledWorldEvent.id,
        ).limit(1)
    )).scalar_one_or_none()


def resolve_scheduled_event(
    event: ScheduledWorldEvent,
    *,
    world_minute: int,
) -> None:
    event.status = "resolved"
    event.resolved_at_minute = world_minute
    event.last_error = None


def cancel_scheduled_event(
    event: ScheduledWorldEvent,
    *,
    world_minute: int,
    reason: str,
) -> None:
    event.status = "cancelled"
    event.resolved_at_minute = world_minute
    event.last_error = reason


async def chronicle_once(
    session: AsyncSession,
    *,
    dedupe_key: str | None,
    kind: str,
    world_minute: int,
    summary: str,
    actor_id: str | None = None,
    target_id: str | None = None,
    room_id: int | None = None,
    visibility: str = "witnessed",
    witnesses: Iterable[str] = (),
    payload: Mapping[str, object] | None = None,
) -> tuple[WorldEvent, bool]:
    if dedupe_key is not None:
        existing = (await session.execute(
            select(WorldEvent).where(WorldEvent.dedupe_key == dedupe_key)
        )).scalar_one_or_none()
        if existing is not None:
            return existing, False
    row = WorldEvent(
        dedupe_key=dedupe_key,
        kind=kind,
        world_minute=world_minute,
        actor_id=actor_id,
        target_id=target_id,
        room_id=room_id,
        summary=summary,
        visibility=visibility,
        witnesses=sorted(set(witnesses)),
        payload=dict(payload or {}),
    )
    session.add(row)
    await session.flush()
    return row, True


def memory_from_row(row: NPCMemory) -> Memory:
    return Memory(
        id=row.memory_key,
        owner_id=row.npc_content_id,
        kind=row.kind,
        summary=row.summary,
        tags=frozenset(row.tags or ()),
        importance=float(row.importance),
        confidence=float(row.confidence),
        occurred_at=row.world_minute,
        last_recalled_at=row.last_recalled_minute,
        source_id=row.source_id,
        source_memory_id=row.source_memory_id,
        shareable=bool(row.shareable),
        secrecy=float(row.secrecy),
        cascade_depth=row.cascade_depth,
    )


async def remember_once(
    session: AsyncSession,
    memory: Memory,
    *,
    source_chain: Iterable[str] = (),
    source_event_id: int | None = None,
    expires_at_minute: int | None = None,
    payload: Mapping[str, object] | None = None,
) -> tuple[NPCMemory, bool]:
    existing = (await session.execute(
        select(NPCMemory).where(NPCMemory.memory_key == memory.id)
    )).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = NPCMemory(
        memory_key=memory.id,
        npc_content_id=memory.owner_id,
        kind=memory.kind,
        summary=memory.summary,
        tags=sorted(memory.tags),
        source_chain=list(source_chain),
        source_id=memory.source_id,
        source_memory_id=memory.source_memory_id,
        source_event_id=source_event_id,
        importance=memory.importance,
        confidence=memory.confidence,
        shareable=memory.shareable,
        secrecy=memory.secrecy,
        cascade_depth=memory.cascade_depth,
        world_minute=memory.occurred_at,
        last_recalled_minute=memory.last_recalled_at,
        expires_at_minute=expires_at_minute,
        payload=dict(payload or {}),
    )
    session.add(row)
    await session.flush()
    return row, True


async def memory_rows(
    session: AsyncSession,
    npc_content_id: str,
    *,
    now_minute: int | None = None,
) -> list[NPCMemory]:
    statement = select(NPCMemory).where(
        NPCMemory.npc_content_id == npc_content_id
    )
    rows = list((await session.execute(
        statement.order_by(NPCMemory.world_minute, NPCMemory.id)
    )).scalars())
    if now_minute is None:
        return rows
    return [
        row
        for row in rows
        if row.expires_at_minute is None or row.expires_at_minute > now_minute
    ]


async def reflection_source_rows(
    session: AsyncSession,
    npc_content_id: str,
    *,
    limit: int = 4,
) -> list[NPCMemory]:
    """Load exactly the evidence set used by deterministic reflection.

    ``synthesize_reflection`` ranks non-reflections by importance, recency,
    then stable identity and consumes only four. Loading an NPC's complete,
    ever-growing history at every deliberation produced quadratic long-run
    work without changing the selected evidence.
    """
    if limit <= 0:
        return []
    return list((await session.execute(
        select(NPCMemory).where(
            NPCMemory.npc_content_id == npc_content_id,
            NPCMemory.kind != "reflection",
        ).order_by(
            NPCMemory.importance.desc(),
            NPCMemory.world_minute.desc(),
            NPCMemory.memory_key,
        ).limit(limit)
    )).scalars())


async def retrieve_memory_rows(
    session: AsyncSession,
    npc_content_id: str,
    *,
    query_tags: frozenset[str],
    now_minute: int,
    limit: int = 8,
    mark_recalled: bool = False,
) -> list[NPCMemory]:
    rows = await memory_rows(
        session, npc_content_id, now_minute=now_minute,
    )
    by_key = {row.memory_key: row for row in rows}
    retrieved = retrieve_memories(
        (memory_from_row(row) for row in rows),
        query_tags=query_tags,
        now_minute=now_minute,
        limit=limit,
    )
    result = [by_key[memory.id] for memory in retrieved]
    if mark_recalled:
        for row in result:
            row.last_recalled_minute = now_minute
    return result


async def relationship_row(
    session: AsyncSession,
    *,
    source_id: str,
    target_id: str,
) -> NPCRelationship | None:
    return (await session.execute(
        select(NPCRelationship).where(
            NPCRelationship.source_npc_content_id == source_id,
            NPCRelationship.target_kind == "npc",
            NPCRelationship.target_id == target_id,
        )
    )).scalar_one_or_none()


async def relationship_trust(
    session: AsyncSession,
    *,
    source_id: str,
    target_id: str,
) -> float:
    row = await relationship_row(
        session, source_id=source_id, target_id=target_id,
    )
    return float(row.trust) if row is not None else 0.0


async def apply_social_delta(
    session: AsyncSession,
    *,
    source_id: str,
    target_id: str,
    world_minute: int,
    **deltas: float,
) -> NPCRelationship:
    row = await relationship_row(
        session, source_id=source_id, target_id=target_id,
    )
    if row is None:
        row = NPCRelationship(
            source_npc_content_id=source_id,
            target_kind="npc",
            target_id=target_id,
        )
        session.add(row)
        await session.flush()
    current = Relationship(
        affinity=row.affinity,
        trust=row.trust,
        fear=row.fear,
        respect=row.respect,
        obligation=row.obligation,
        intimacy=row.intimacy,
        grievance=row.grievance,
        familiarity=row.familiarity,
    )
    changed = apply_relationship_delta(current, **deltas)
    for axis in Relationship.__dataclass_fields__:
        setattr(row, axis, getattr(changed, axis))
    row.last_interaction_minute = world_minute
    row.updated_at_minute = world_minute
    return row


async def ensure_goal(
    session: AsyncSession,
    *,
    npc_content_id: str,
    goal_key: str,
    kind: str,
    target_id: str | None,
    priority: float,
    next_deliberation_minute: int,
    world_minute: int,
    context: Mapping[str, object] | None = None,
) -> tuple[NPCGoal, bool]:
    row = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == npc_content_id,
            NPCGoal.goal_key == goal_key,
        )
    )).scalar_one_or_none()
    created = row is None
    if row is None:
        row = NPCGoal(
            npc_content_id=npc_content_id,
            goal_key=goal_key,
            kind=kind,
            target_id=target_id,
            priority=priority,
            status="active",
            created_at_minute=world_minute,
            next_deliberation_minute=next_deliberation_minute,
            context={"authored": dict(context or {})},
        )
        session.add(row)
        await session.flush()
        return row, True
    # Authored definition fields synchronize without erasing lived progress,
    # status, failure, or runtime context.
    row.kind = kind
    row.target_id = target_id
    row.priority = priority
    row.next_deliberation_minute = next_deliberation_minute
    existing_context = dict(row.context or {})
    existing_context["authored"] = dict(context or {})
    row.context = existing_context
    return row, created


async def goals_for_npc(
    session: AsyncSession,
    npc_content_id: str,
) -> list[NPCGoal]:
    return list((await session.execute(
        select(NPCGoal)
        .where(NPCGoal.npc_content_id == npc_content_id)
        .order_by(NPCGoal.goal_key)
    )).scalars())


async def living_npcs(session: AsyncSession) -> list[NPCRow]:
    return list((await session.execute(
        select(NPCRow)
        .where(NPCRow.content_id.is_not(None), NPCRow.is_alive.is_(True))
        .order_by(NPCRow.content_id, NPCRow.id)
    )).scalars())


async def npc_by_content_id(
    session: AsyncSession,
    content_id: str,
) -> NPCRow | None:
    return (await session.execute(
        select(NPCRow).where(NPCRow.content_id == content_id)
    )).scalar_one_or_none()


async def roommates(
    session: AsyncSession,
    *,
    room_id: int,
    excluding_id: str | None = None,
) -> list[NPCRow]:
    statement = select(NPCRow).where(
        NPCRow.room_id == room_id,
        NPCRow.is_alive.is_(True),
        NPCRow.content_id.is_not(None),
    )
    if excluding_id is not None:
        statement = statement.where(NPCRow.content_id != excluding_id)
    return list((await session.execute(
        statement.order_by(NPCRow.content_id, NPCRow.id)
    )).scalars())


async def room_id_by_content(
    session: AsyncSession,
) -> dict[str, int]:
    rows = (await session.execute(
        select(Room.content_id, Room.id)
        .where(Room.content_id.is_not(None))
        .order_by(Room.content_id)
    )).all()
    result = {content_id: room_id for content_id, room_id in rows}
    for location_id, room_content_id in _LOCATION_ROOM_ALIASES.items():
        room_id = result.get(room_content_id)
        if room_id is not None:
            # A dedicated authored room always wins once it exists; aliases
            # are compatibility shims only for locations that still share a
            # playable grid.
            result.setdefault(location_id, room_id)
    return result


async def route_edges(
    session: AsyncSession,
    *,
    travel_minutes: int,
) -> list[RouteEdge]:
    rows = list((await session.execute(
        select(RoomConnection).order_by(
            RoomConnection.from_room_id,
            RoomConnection.to_room_id,
            RoomConnection.id,
        )
    )).scalars())
    return [
        RouteEdge(
            from_room_id=row.from_room_id,
            to_room_id=row.to_room_id,
            travel_minutes=travel_minutes,
        )
        for row in rows
    ]


async def has_pending_actor_action(
    session: AsyncSession,
    *,
    actor_id: str,
    kinds: Iterable[str],
) -> bool:
    return (await session.execute(
        select(ScheduledWorldEvent.id).where(
            ScheduledWorldEvent.actor_id == actor_id,
            ScheduledWorldEvent.kind.in_(tuple(kinds)),
            ScheduledWorldEvent.status == "pending",
        ).limit(1)
    )).first() is not None


async def pending_actor_event(
    session: AsyncSession,
    *,
    actor_id: str,
    kinds: Iterable[str],
) -> ScheduledWorldEvent | None:
    return (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.actor_id == actor_id,
            ScheduledWorldEvent.kind.in_(tuple(kinds)),
            ScheduledWorldEvent.status == "pending",
        ).order_by(
            ScheduledWorldEvent.due_minute,
            ScheduledWorldEvent.priority,
            ScheduledWorldEvent.id,
        ).limit(1)
    )).scalar_one_or_none()


async def connection_exists(
    session: AsyncSession,
    *,
    from_room_id: int,
    to_room_id: int,
) -> bool:
    return (await session.execute(
        select(RoomConnection.id).where(
            RoomConnection.from_room_id == from_room_id,
            RoomConnection.to_room_id == to_room_id,
        ).limit(1)
    )).first() is not None


async def arrival_position(
    session: AsyncSession,
    *,
    room_id: int,
    from_room_id: int,
) -> tuple[int, int] | None:
    """Choose a stable free floor tile near the returning connection."""
    room = await session.get(Room, room_id)
    if room is None:
        return None
    reverse = (await session.execute(
        select(RoomConnection).where(
            RoomConnection.from_room_id == room_id,
            RoomConnection.to_room_id == from_room_id,
        ).order_by(RoomConnection.id)
    )).scalars().first()
    anchor = (
        (reverse.from_x, reverse.from_y)
        if reverse is not None
        else (room.width // 2, room.height // 2)
    )
    occupied = {
        (row.x, row.y)
        for row in (await session.execute(
            select(NPCRow).where(
                NPCRow.room_id == room_id,
                NPCRow.is_alive.is_(True),
            )
        )).scalars()
    }
    object_tiles = {
        (obj.get("x"), obj.get("y"))
        for obj in (room.objects or [])
        if isinstance(obj, dict)
    }
    spawn_tiles = {
        (int(point[0]), int(point[1]))
        for point in (room.spawn_points or [])
        if isinstance(point, (list, tuple)) and len(point) == 2
    }
    candidates = [
        (x, y)
        for y, terrain_row in enumerate(room.terrain)
        for x, tile in enumerate(terrain_row)
        if tile == "."
        and (x, y) not in occupied
        and (x, y) not in object_tiles
        and (x, y) not in spawn_tiles
    ]
    if not candidates:
        candidates = [
            (x, y)
            for y, terrain_row in enumerate(room.terrain)
            for x, tile in enumerate(terrain_row)
            if tile == "." and (x, y) not in occupied
        ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda point: (
            abs(point[0] - anchor[0]) + abs(point[1] - anchor[1]),
            point[1],
            point[0],
        ),
    )


async def ensure_fact(
    session: AsyncSession,
    *,
    fact_key: str,
    subject_id: str,
    predicate: str,
    value: Mapping[str, object],
    confidence: float,
    visibility: str,
    world_minute: int,
) -> tuple[WorldFact, bool]:
    row = (await session.execute(
        select(WorldFact).where(WorldFact.fact_key == fact_key)
    )).scalar_one_or_none()
    if row is not None:
        return row, False
    row = WorldFact(
        fact_key=fact_key,
        subject_id=subject_id,
        predicate=predicate,
        value=dict(value),
        confidence=confidence,
        visibility=visibility,
        established_at_minute=world_minute,
        updated_at_minute=world_minute,
    )
    session.add(row)
    await session.flush()
    return row, True


async def set_fact(
    session: AsyncSession,
    *,
    fact_key: str,
    subject_id: str,
    predicate: str,
    value: Mapping[str, object],
    confidence: float,
    visibility: str,
    world_minute: int,
    source_event_id: int | None = None,
    expires_at_minute: int | None = None,
) -> tuple[WorldFact, bool]:
    """Create or deliberately replace a mutable structured world truth.

    ``ensure_fact`` is intentionally immutable and remains the right tool for
    seed facts. Authored consequences need an explicit update operation so a
    later branch can supersede an earlier state without creating conflicting
    facts under different keys.
    """
    row = (await session.execute(
        select(WorldFact).where(WorldFact.fact_key == fact_key)
    )).scalar_one_or_none()
    inserted = row is None
    if row is None:
        row = WorldFact(
            fact_key=fact_key,
            established_at_minute=world_minute,
        )
        session.add(row)
    row.subject_id = subject_id
    row.predicate = predicate
    row.value = dict(value)
    row.confidence = confidence
    row.visibility = visibility
    row.updated_at_minute = world_minute
    row.source_event_id = source_event_id
    row.expires_at_minute = expires_at_minute
    await session.flush()
    return row, inserted


async def record_npc_death(
    session: AsyncSession,
    *,
    npc_content_id: str,
    npc_name: str,
    max_hp: int | None,
    room_id: int,
    world_minute: int,
    summary: str | None = None,
    killer_id: str | None = None,
    source: str,
    witnesses: Iterable[str] = (),
) -> tuple[WorldEvent, bool]:
    """Persist one individual's death and the evidence players can discover."""
    account = summary or f"{npc_name} died."
    witness_ids = tuple(sorted(set(witnesses)))
    event, inserted = await chronicle_once(
        session,
        dedupe_key=f"npc-death:{npc_content_id}",
        kind="npc_died",
        world_minute=world_minute,
        summary=account,
        actor_id=killer_id,
        target_id=npc_content_id,
        room_id=room_id,
        visibility="witnessed" if witness_ids else "public_aftermath",
        witnesses=witness_ids,
        payload={"source": source},
    )
    await set_fact(
        session,
        fact_key=f"npc-fate:{npc_content_id}",
        subject_id=npc_content_id,
        predicate="fate",
        value={
            "is_alive": False,
            "room_id": room_id,
            "source": source,
        },
        confidence=1.0,
        visibility="hidden",
        world_minute=world_minute,
        source_event_id=event.id,
    )
    health_value: dict[str, object] = {"hp": 0, "is_alive": False}
    if max_hp is not None:
        health_value["max_hp"] = max_hp
    await set_fact(
        session,
        fact_key=f"npc-health:{npc_content_id}",
        subject_id=npc_content_id,
        predicate="health",
        value=health_value,
        confidence=1.0,
        visibility="hidden",
        world_minute=world_minute,
        source_event_id=event.id,
    )
    await chronicle_once(
        session,
        dedupe_key=f"npc-death-evidence:{npc_content_id}",
        kind="evidence_left",
        world_minute=world_minute,
        summary=account,
        target_id=npc_content_id,
        room_id=room_id,
        visibility="discoverable",
        payload={"source": source, "death_event_id": event.id},
    )
    return event, inserted
