"""Player-visible evidence from the private living-world simulation.

NPC goals and world facts stay hidden. This module exposes only people a
player has met, rumors someone actually told them, and witnessed chronology.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.actor_defs import get_actor_art
from backend.living_world.clock import day_phase, world_day
from backend.living_world.memory import Memory
from backend.living_world.store import (
    chronicle_once,
    remember_once,
    retrieve_memory_rows,
)
from backend.living_world_content import load_living_world_content
from backend.models import (
    NPCGoal,
    NPCRelationship,
    NPCRow,
    PlayerKnowledge,
    PlayerWorldState,
    Room,
    WorldEvent,
    WorldState,
)


@lru_cache(maxsize=1)
def _content():
    return load_living_world_content()


def _phase(world_minute: int) -> str:
    phase = day_phase(world_minute)
    return {
        "midday": "afternoon",
        "evening": "dusk",
    }.get(phase, phase)


def _time_view(world_minute: int) -> dict:
    phase = _phase(world_minute)
    day = world_day(world_minute) + 1
    return {
        "world_minute": world_minute,
        "day": day,
        "phase": phase,
        "label": f"Day {day}, {phase.replace('_', ' ')}",
    }


def world_time_view(world_minute: int) -> dict:
    return _time_view(world_minute)


def _when(world_minute: int) -> str:
    return _time_view(world_minute)["label"]


async def observe_room(
    session: AsyncSession,
    *,
    player_id: str,
    room_id: int,
    world_minute: int,
) -> int:
    """Remember every living person visibly present in the player's room."""
    room = await session.get(Room, room_id)
    people = (await session.execute(
        select(NPCRow).where(
            NPCRow.room_id == room_id,
            NPCRow.is_alive.is_(True),
            NPCRow.content_id.is_not(None),
        )
    )).scalars().all()
    created = 0
    for npc in people:
        key = str(npc.content_id)
        relation = (await session.execute(
            select(NPCRelationship).where(
                NPCRelationship.source_npc_content_id == key,
                NPCRelationship.target_kind == "player",
                NPCRelationship.target_id == player_id,
            )
        )).scalars().first()
        existing = (await session.execute(
            select(PlayerKnowledge).where(
                PlayerKnowledge.player_id == player_id,
                PlayerKnowledge.kind == "person",
                PlayerKnowledge.knowledge_key == key,
            )
        )).scalars().first()
        payload = {
            "npc_content_id": key,
            "last_seen_room_id": room_id,
            "last_seen_room_name": room.name if room else "Unknown place",
            "last_seen_minute": world_minute,
            # Snapshot subjective information while it is observable. Never
            # derive an off-screen person's current state from the private
            # simulation when constructing a player payload.
            "relationship": _relationship_tone(relation),
            "relationship_note": _relationship_note(relation),
        }
        if existing is None:
            session.add(PlayerKnowledge(
                player_id=player_id,
                kind="person",
                knowledge_key=key,
                title=npc.name,
                body=npc.persona.get("role", "traveller"),
                provenance="witnessed",
                learned_at_minute=world_minute,
                place=room.name if room else None,
                payload=payload,
            ))
            created += 1
        else:
            existing.title = npc.name
            existing.body = npc.persona.get("role", existing.body)
            existing.place = room.name if room else existing.place
            existing.payload = payload
    evidence = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.room_id == room_id,
            WorldEvent.kind == "evidence_left",
            WorldEvent.visibility == "discoverable",
        ).order_by(WorldEvent.world_minute, WorldEvent.id)
    )).scalars().all()
    for event in evidence:
        key = f"evidence:{event.id}"
        existing = (await session.execute(
            select(PlayerKnowledge).where(
                PlayerKnowledge.player_id == player_id,
                PlayerKnowledge.kind == "clue",
                PlayerKnowledge.knowledge_key == key,
            )
        )).scalars().first()
        if existing is None:
            session.add(PlayerKnowledge(
                player_id=player_id,
                kind="clue",
                knowledge_key=key,
                title="Something left behind",
                body=event.summary,
                provenance="found",
                learned_at_minute=world_minute,
                place=room.name if room else None,
                payload={
                    "source_event_id": event.id,
                    "occurred_at_minute": event.world_minute,
                },
            ))
            created += 1
    return created


async def dialogue_memory_context(
    session: AsyncSession,
    *,
    npc_content_id: str,
    player_id: str,
    text: str,
    world_minute: int,
) -> dict:
    tags = {"conversation", "person"}
    lowered = text.lower()
    for needle, tag in (
        ("rot", "rot"),
        ("road", "road"),
        ("carriage", "carriage"),
        ("family", "family"),
        ("promise", "promise"),
        ("danger", "danger"),
    ):
        if needle in lowered:
            tags.add(tag)
    memories = await retrieve_memory_rows(
        session,
        npc_content_id,
        query_tags=frozenset(tags),
        now_minute=world_minute,
        limit=6,
        mark_recalled=True,
    )
    relation = (await session.execute(
        select(NPCRelationship).where(
            NPCRelationship.source_npc_content_id == npc_content_id,
            NPCRelationship.target_kind == "player",
            NPCRelationship.target_id == player_id,
        )
    )).scalars().first()
    return {
        "memories": [row.summary for row in memories],
        "relationship": _relationship_tone(relation),
    }


async def record_player_conversation(
    session: AsyncSession,
    *,
    player_id: str,
    player_name: str,
    npc_content_id: str,
    npc_name: str,
    room_id: int,
    player_text: str,
    npc_text: str,
    world_minute: int,
) -> PlayerKnowledge | None:
    """Persist dialogue consequences and reveal at most one held rumor."""
    digest = hashlib.blake2b(
        (
            f"{player_id}\x1f{npc_content_id}\x1f{world_minute}\x1f"
            f"{player_text}\x1f{npc_text}"
        ).encode("utf-8"),
        digest_size=10,
    ).hexdigest()
    event, _ = await chronicle_once(
        session,
        dedupe_key=f"dialogue:{digest}",
        kind="player_npc_conversation",
        world_minute=world_minute,
        summary=f"{player_name} spoke with {npc_name}.",
        actor_id=player_id,
        target_id=npc_content_id,
        room_id=room_id,
        visibility="private",
        witnesses=(player_id,),
        payload={"npc_name": npc_name},
    )
    await remember_once(
        session,
        Memory(
            id=f"memory:dialogue:{digest}",
            owner_id=npc_content_id,
            kind="conversation",
            summary=(
                f"{player_name} said “{player_text[:180]}” and I answered "
                f"“{npc_text[:180]}”."
            ),
            tags=frozenset({"conversation", "person"}),
            importance=4.0,
            confidence=1.0,
            occurred_at=world_minute,
            source_id=player_id,
        ),
        source_event_id=event.id,
        payload={"player_id": player_id},
    )
    await _warm_relationship(
        session,
        npc_content_id=npc_content_id,
        player_id=player_id,
        world_minute=world_minute,
    )
    return await _learn_next_rumor(
        session,
        player_id=player_id,
        npc_content_id=npc_content_id,
        npc_name=npc_name,
        world_minute=world_minute,
    )


async def _warm_relationship(
    session: AsyncSession,
    *,
    npc_content_id: str,
    player_id: str,
    world_minute: int,
) -> None:
    relation = (await session.execute(
        select(NPCRelationship).where(
            NPCRelationship.source_npc_content_id == npc_content_id,
            NPCRelationship.target_kind == "player",
            NPCRelationship.target_id == player_id,
        )
    )).scalars().first()
    if relation is None:
        relation = NPCRelationship(
            source_npc_content_id=npc_content_id,
            target_kind="player",
            target_id=player_id,
        )
        session.add(relation)
    relation.familiarity = min(100.0, float(relation.familiarity or 0.0) + 4.0)
    relation.trust = min(100.0, float(relation.trust or 0.0) + 0.5)
    relation.affinity = min(100.0, float(relation.affinity or 0.0) + 0.25)
    relation.last_interaction_minute = world_minute
    relation.updated_at_minute = world_minute


async def _learn_next_rumor(
    session: AsyncSession,
    *,
    player_id: str,
    npc_content_id: str,
    npc_name: str,
    world_minute: int,
) -> PlayerKnowledge | None:
    content = _content()
    profile = content.npc_profiles.get(npc_content_id)
    if profile is None:
        return None
    for reference in profile.get("belief_refs", []):
        rumor_id = reference["rumor_id"]
        existing = (await session.execute(
            select(PlayerKnowledge).where(
                PlayerKnowledge.player_id == player_id,
                PlayerKnowledge.kind == "rumor",
                PlayerKnowledge.knowledge_key == rumor_id,
            )
        )).scalars().first()
        if existing is not None:
            continue
        rumor = content.rumors[rumor_id]
        belief = next(
            item
            for item in rumor["beliefs"]
            if item["id"] == reference["belief_id"]
        )
        row = PlayerKnowledge(
            player_id=player_id,
            kind="rumor",
            knowledge_key=rumor_id,
            title=rumor["topic"],
            body=belief["claim"],
            provenance="heard",
            learned_at_minute=world_minute,
            source=npc_name,
            payload={
                "related_npc_ids": [npc_content_id],
                "belief_id": belief["id"],
                "confidence": belief["confidence"],
            },
        )
        session.add(row)
        return row
    return None


async def world_sync(
    session: AsyncSession,
    *,
    player_id: str,
    current_room_id: int,
) -> dict:
    world = await session.get(WorldState, 1)
    minute = world.world_minute if world else 0
    await observe_room(
        session,
        player_id=player_id,
        room_id=current_room_id,
        world_minute=minute,
    )
    player_state = await session.get(PlayerWorldState, player_id)
    if player_state is None:
        player_state = PlayerWorldState(
            player_id=player_id,
            last_seen_world_minute=minute,
            last_seen_event_id=0,
        )
        session.add(player_state)
        await session.flush()
    away_after = player_state.last_seen_world_minute

    knowledge = (await session.execute(
        select(PlayerKnowledge).where(
            PlayerKnowledge.player_id == player_id
        ).order_by(PlayerKnowledge.learned_at_minute, PlayerKnowledge.id)
    )).scalars().all()
    rumors = [
        {
            "id": row.knowledge_key,
            "title": row.title,
            "body": row.body,
            "provenance": row.provenance,
            "learned_at": _when(row.learned_at_minute),
            "source": row.source,
            "place": row.place,
            "related_npc_ids": row.payload.get("related_npc_ids", []),
            "unread": row.learned_at_minute > away_after,
        }
        for row in knowledge
        if row.kind == "rumor"
    ]
    known_people = await _known_people_view(
        session,
        player_id=player_id,
        rows=[row for row in knowledge if row.kind == "person"],
        known_rumor_ids={
            row.knowledge_key for row in knowledge if row.kind == "rumor"
        },
        current_room_id=current_room_id,
        away_after=away_after,
    )
    events = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.id > player_state.last_seen_event_id
        ).order_by(WorldEvent.world_minute, WorldEvent.id).limit(80)
    )).scalars().all()
    visible = [
        event
        for event in events
        if event.visibility == "public"
        or player_id in (event.witnesses or [])
        or event.actor_id == player_id
    ][-30:]
    chronicle = [
        {
            "id": f"event:{event.id}",
            "world_minute": event.world_minute,
            "happened_at": _when(event.world_minute),
            "title": _event_title(event),
            "body": event.summary,
            "provenance": (
                "witnessed" if player_id in (event.witnesses or []) else "heard"
            ),
            "place": await _room_name(session, event.room_id),
            "actor_world_ids": [
                actor for actor in (event.actor_id, event.target_id) if actor
            ],
            "while_away": event.world_minute > away_after,
            "unread": event.world_minute > away_after,
        }
        for event in visible
    ]
    chronicle.extend({
        "id": f"knowledge:{row.id}",
        "world_minute": row.learned_at_minute,
        "happened_at": _when(row.learned_at_minute),
        "title": row.title,
        "body": row.body,
        "provenance": "found",
        "place": row.place,
        "actor_world_ids": [],
        "while_away": row.learned_at_minute > away_after,
        "unread": row.learned_at_minute > away_after,
    } for row in knowledge if row.kind == "clue")
    chronicle.sort(key=lambda entry: (entry["world_minute"], entry["id"]))
    player_state.last_seen_world_minute = minute
    if events:
        player_state.last_seen_event_id = max(event.id for event in events)
    await session.commit()
    return {
        "type": "world_sync",
        "time": _time_view(minute),
        "rumors": rumors,
        "chronicle": chronicle,
        "known_people": known_people,
    }


async def _known_people_view(
    session: AsyncSession,
    *,
    player_id: str,
    rows: list[PlayerKnowledge],
    known_rumor_ids: set[str],
    current_room_id: int,
    away_after: int,
) -> list[dict]:
    result: list[dict] = []
    content = _content()
    for known in rows:
        npc = (await session.execute(
            select(NPCRow).where(NPCRow.content_id == known.knowledge_key)
        )).scalars().first()
        if npc is None:
            continue
        relation = (await session.execute(
            select(NPCRelationship).where(
                NPCRelationship.source_npc_content_id == known.knowledge_key,
                NPCRelationship.target_kind == "player",
                NPCRelationship.target_id == player_id,
            )
        )).scalars().first()
        goal = (await session.execute(
            select(NPCGoal).where(
                NPCGoal.npc_content_id == known.knowledge_key,
                NPCGoal.status == "active",
            ).order_by(NPCGoal.priority.desc())
        )).scalars().first()
        art = get_actor_art(npc.persona.get("art_id", ""))
        profile = content.npc_profiles.get(known.knowledge_key, {})
        topics = []
        for reference in profile.get("belief_refs", []):
            if reference["rumor_id"] not in known_rumor_ids:
                continue
            rumor = content.rumors[reference["rumor_id"]]
            topics.append({
                "id": reference["rumor_id"],
                "label": rumor["topic"],
                "prompt": f"What have you heard about {rumor['topic'].rstrip('.?').lower()}?",
            })
            if len(topics) == 3:
                break
        present = npc.room_id == current_room_id
        availability = (
            "dead" if present and not npc.is_alive
            else "present" if present
            else "away"
        )
        relationship = (
            _relationship_tone(relation)
            if present
            else known.payload.get("relationship", "unfamiliar")
        )
        relationship_note = (
            _relationship_note(relation)
            if present
            else known.payload.get("relationship_note")
        )
        result.append({
            "world_id": known.knowledge_key,
            "name": npc.name,
            "role": npc.persona.get("role", known.body),
            "image": art.image if art else None,
            "relationship": relationship,
            "relationship_note": relationship_note,
            "availability": availability,
            "activity": _activity_view(goal, availability) if present else None,
            "last_seen": {
                "room_name": known.payload.get(
                    "last_seen_room_name", known.place or "Unknown place"
                ),
                "at": _when(int(known.payload.get(
                    "last_seen_minute", known.learned_at_minute
                ))),
            },
            "dialogue_topics": topics,
            "unread": known.learned_at_minute > away_after,
        })
    return sorted(result, key=lambda item: (item["availability"] != "present", item["name"]))


def _relationship_tone(row: NPCRelationship | None) -> str:
    if row is None or (row.familiarity or 0) < 5:
        return "unfamiliar"
    score = (
        (row.trust or 0) + (row.affinity or 0)
        - (row.fear or 0) - (row.grievance or 0)
    )
    if score < -45:
        return "hostile"
    if score < -8:
        return "wary"
    if score < 30:
        return "cordial"
    if score < 90:
        return "trusting"
    return "devoted"


def _relationship_note(row: NPCRelationship | None) -> str | None:
    if row is None:
        return None
    if (row.grievance or 0) > 20:
        return "They remember a grievance."
    if (row.obligation or 0) > 25:
        return "They feel they owe you something."
    if (row.familiarity or 0) >= 20:
        return "You are no longer a stranger to them."
    return "They remember speaking with you."


def _activity_view(goal: NPCGoal | None, availability: str) -> dict:
    if availability == "dead":
        return {"kind": "unknown", "label": "No longer living"}
    if goal is None:
        return {"kind": "idle", "label": "Following their ordinary routine"}
    kind = "travelling" if goal.kind == "travel" else "working"
    return {
        "kind": kind,
        "label": (
            "Travelling somewhere beyond your sight"
            if kind == "travelling"
            else "Occupied with private concerns"
        ),
    }


def _event_title(event: WorldEvent) -> str:
    return {
        "player_npc_conversation": "Words exchanged",
        "npc_moved": "Someone moved on",
        "rumor_shared": "A rumor changed hands",
        "npc_goal_changed": "A life changed direction",
        "missed_opportunity": "An opportunity passed",
    }.get(event.kind, event.kind.replace("_", " ").title())


async def _room_name(session: AsyncSession, room_id: int | None) -> str | None:
    if room_id is None:
        return None
    room = await session.get(Room, room_id)
    return room.name if room else None
