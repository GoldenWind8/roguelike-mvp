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
    ScheduledWorldEvent,
    WorldEvent,
    WorldState,
)
from backend.object_defs import ObjectDiscovery


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


_CHRONICLE_SCAN_LIMIT = 400
_CHRONICLE_HISTORY_LIMIT = 120


def _pending_arrival_for_npc_row():
    """Correlated physical-presence guard for an ``NPCRow`` query."""
    return select(ScheduledWorldEvent.id).where(
        ScheduledWorldEvent.actor_id == NPCRow.content_id,
        ScheduledWorldEvent.kind == "npc_arrive_room",
        ScheduledWorldEvent.status == "pending",
    ).exists()


async def observe_room(
    session: AsyncSession,
    *,
    player_id: str,
    room_id: int,
    world_minute: int,
) -> int:
    """Remember people and visible changes in the player's current room.

    The snapshot is deliberately evidence-shaped: a wound or body can remain
    known after the player leaves, while an off-screen simulation change never
    overwrites what the player last observed.
    """
    room = await session.get(Room, room_id)
    people = (await session.execute(
        select(NPCRow).where(
            NPCRow.room_id == room_id,
            NPCRow.content_id.is_not(None),
            ~_pending_arrival_for_npc_row(),
        )
    )).scalars().all()
    known_rows = (await session.execute(
        select(PlayerKnowledge).where(
            PlayerKnowledge.player_id == player_id,
            PlayerKnowledge.kind == "person",
        )
    )).scalars().all()
    known_by_key = {row.knowledge_key: row for row in known_rows}
    visible_keys: set[str] = set()
    created = 0
    for npc in people:
        key = str(npc.content_id)
        visible_keys.add(key)
        relation = (await session.execute(
            select(NPCRelationship).where(
                NPCRelationship.source_npc_content_id == key,
                NPCRelationship.target_kind == "player",
                NPCRelationship.target_id == player_id,
            )
        )).scalars().first()
        existing = known_by_key.get(key)
        condition = _condition_snapshot(npc)
        availability = "dead" if not npc.is_alive else "present"
        last_seen_note = _condition_note(condition["kind"])
        previous = dict(existing.payload or {}) if existing else {}
        changed = existing is None or any((
            previous.get("last_seen_room_id") != room_id,
            previous.get("availability") != availability,
            previous.get("condition") != condition,
            previous.get("relationship") != _relationship_tone(relation),
            previous.get("relationship_note") != _relationship_note(relation),
        ))
        revision = int(previous.get("revision", 0)) + (1 if changed else 0)
        payload = {
            **previous,
            "npc_content_id": key,
            "last_seen_room_id": room_id,
            "last_seen_room_name": room.name if room else "Unknown place",
            "last_seen_minute": world_minute,
            # Snapshot subjective information while it is observable. Never
            # derive an off-screen person's current state from the private
            # simulation when constructing a player payload.
            "relationship": _relationship_tone(relation),
            "relationship_note": _relationship_note(relation),
            "availability": availability,
            "condition": condition,
            "last_seen_note": last_seen_note,
            "revision": max(1, revision),
            "updated_at_minute": (
                world_minute if changed
                else int(previous.get("updated_at_minute", world_minute))
            ),
        }
        if existing is None:
            existing = PlayerKnowledge(
                player_id=player_id,
                kind="person",
                knowledge_key=key,
                title=npc.name,
                body=npc.persona.get("role", "traveller"),
                provenance="witnessed",
                learned_at_minute=world_minute,
                place=room.name if room else None,
                payload=payload,
            )
            session.add(existing)
            known_by_key[key] = existing
            created += 1
        else:
            existing.title = npc.name
            existing.body = npc.persona.get("role", existing.body)
            existing.place = room.name if room else existing.place
            existing.payload = payload

    # Returning to someone's last-known place and finding it empty is evidence
    # of an absence, but not evidence of where they went or whether they live.
    for known in known_rows:
        previous = dict(known.payload or {})
        if (
            known.knowledge_key in visible_keys
            or previous.get("last_seen_room_id") != room_id
            or previous.get("availability") in {"away", "travelling", "dead"}
        ):
            continue
        previous["availability"] = "away"
        previous["last_seen_note"] = "They were gone when you returned."
        previous["revision"] = int(previous.get("revision", 0)) + 1
        previous["updated_at_minute"] = world_minute
        known.payload = previous

    evidence = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.room_id == room_id,
            WorldEvent.kind == "evidence_left",
            WorldEvent.visibility == "discoverable",
        ).order_by(WorldEvent.world_minute, WorldEvent.id)
    )).scalars().all()
    for event in evidence:
        expires_at = (event.payload or {}).get("expires_at_minute")
        if (
            isinstance(expires_at, int)
            and not isinstance(expires_at, bool)
            and world_minute >= expires_at
        ):
            continue
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


async def record_object_discovery(
    session: AsyncSession,
    *,
    player_id: str,
    room_id: int,
    object_id: str,
    discovery: ObjectDiscovery,
    world_minute: int,
) -> tuple[PlayerKnowledge, bool]:
    """Remember one authored environmental clue on deliberate inspection.

    Object descriptions remain ordinary scenery.  Only definitions carrying
    explicit discovery metadata cross into the durable Chronicle, which keeps
    exploration meaningful without turning every prop into a tracked task.
    """
    existing = (await session.execute(
        select(PlayerKnowledge).where(
            PlayerKnowledge.player_id == player_id,
            PlayerKnowledge.kind == "clue",
            PlayerKnowledge.knowledge_key == discovery.key,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing, False
    room = await session.get(Room, room_id)
    row = PlayerKnowledge(
        player_id=player_id,
        kind="clue",
        knowledge_key=discovery.key,
        title=discovery.title,
        body=discovery.summary,
        provenance="found",
        learned_at_minute=world_minute,
        source=object_id,
        place=room.name if room else None,
        payload={
            "object_id": object_id,
            "tags": list(discovery.tags),
        },
    )
    session.add(row)
    await session.flush()
    return row, True


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
    commit: bool = True,
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
    initial_sync = player_state is None
    if player_state is None:
        player_state = PlayerWorldState(
            player_id=player_id,
            last_seen_world_minute=minute,
            last_seen_event_id=0,
        )
        session.add(player_state)
        await session.flush()
    away_after = player_state.last_seen_world_minute
    preferences = dict(player_state.preferences or {})
    knowledge_cursor = int(preferences.get("last_seen_knowledge_id", 0))
    seen_person_revisions = {
        str(key): int(value)
        for key, value in dict(
            preferences.get("known_person_revisions", {})
        ).items()
    }
    retained_event_ids = [
        int(event_id)
        for event_id in preferences.get("chronicle_event_ids", [])
    ][-_CHRONICLE_HISTORY_LIMIT:]

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
            "unread": not initial_sync and row.id > knowledge_cursor,
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
        seen_revisions=seen_person_revisions,
        suppress_unread=initial_sync,
    )
    # Chronicle history is returned as a bounded durable view, rather than
    # only as a one-shot delta. Reloading the client therefore cannot erase a
    # public event the player had already learned.
    if initial_sync:
        scanned_events = (await session.execute(
            select(WorldEvent).order_by(WorldEvent.id.desc()).limit(
                _CHRONICLE_SCAN_LIMIT
            )
        )).scalars().all()
        scanned_events.reverse()
    else:
        # Consume unseen events from the durable cursor in ascending chunks.
        # Taking the latest global rows here would let a burst of private
        # simulation noise permanently bury an earlier public or witnessed
        # event before the player had any chance to learn it.
        scanned_events = (await session.execute(
            select(WorldEvent).where(
                WorldEvent.id > player_state.last_seen_event_id
            ).order_by(WorldEvent.id).limit(_CHRONICLE_SCAN_LIMIT)
        )).scalars().all()
    # Local aftermath becomes eligible when a player later visits its room,
    # even if the event predates their global scan cursor.
    local_aftermath = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.room_id == current_room_id,
            WorldEvent.visibility == "public_aftermath",
            WorldEvent.kind != "authored_conversation",
        ).order_by(WorldEvent.id.desc()).limit(_CHRONICLE_HISTORY_LIMIT)
    )).scalars().all()
    retained_events = (
        (await session.execute(
            select(WorldEvent).where(WorldEvent.id.in_(retained_event_ids))
        )).scalars().all()
        if retained_event_ids
        else []
    )
    known_person_ids = {
        row.knowledge_key for row in knowledge if row.kind == "person"
    }
    candidate_events = {
        event.id: event
        for event in (*scanned_events, *local_aftermath)
    }
    newly_eligible = [
        event
        for event in candidate_events.values()
        if _event_visible_to_player(
            event,
            player_id=player_id,
            current_room_id=current_room_id,
        )
    ]
    learned_by_id = {event.id: event for event in retained_events}
    learned_by_id.update({event.id: event for event in newly_eligible})
    visible = sorted(
        learned_by_id.values(),
        key=lambda event: (event.world_minute, event.id),
    )[-_CHRONICLE_HISTORY_LIMIT:]
    previously_retained = set(retained_event_ids)
    chronicle = [
        {
            "id": f"event:{event.id}",
            "world_minute": event.world_minute,
            "happened_at": _when(event.world_minute),
            "title": _event_title(event),
            "body": event.summary,
            "provenance": _event_provenance(
                event,
                player_id=player_id,
                current_room_id=current_room_id,
                previously_learned=event.id in previously_retained,
            ),
            "place": await _room_name(session, event.room_id),
            "actor_world_ids": _visible_event_actor_ids(
                event,
                player_id=player_id,
                known_person_ids=known_person_ids,
            ),
            "while_away": event.world_minute > away_after,
            "unread": not initial_sync and event.id not in previously_retained,
        }
        for event in visible
    ]
    chronicle.extend({
        "id": f"knowledge:{row.id}",
        "world_minute": row.learned_at_minute,
        "happened_at": _when(row.learned_at_minute),
        "title": row.title,
        "body": row.body,
        "provenance": row.provenance,
        "place": row.place,
        "actor_world_ids": [],
        "while_away": row.learned_at_minute > away_after,
        "unread": not initial_sync and row.id > knowledge_cursor,
    } for row in knowledge if row.kind == "clue")
    chronicle.sort(key=lambda entry: (entry["world_minute"], entry["id"]))
    player_state.last_seen_world_minute = minute
    if scanned_events:
        player_state.last_seen_event_id = max(event.id for event in scanned_events)
    if knowledge:
        preferences["last_seen_knowledge_id"] = max(row.id for row in knowledge)
    preferences["known_person_revisions"] = {
        row.knowledge_key: int((row.payload or {}).get("revision", 1))
        for row in knowledge
        if row.kind == "person"
    }
    preferences["chronicle_event_ids"] = [event.id for event in visible]
    player_state.preferences = preferences
    if commit:
        await session.commit()
    else:
        await session.flush()
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
    seen_revisions: dict[str, int],
    suppress_unread: bool,
) -> list[dict]:
    result: list[dict] = []
    content = _content()
    current_room_content_id = await session.scalar(
        select(Room.content_id).where(Room.id == current_room_id)
    )
    identities = tuple(row.knowledge_key for row in rows)
    npc_presence = {
        npc.content_id: (npc, bool(in_transit))
        for npc, in_transit in (await session.execute(
            select(
                NPCRow,
                _pending_arrival_for_npc_row().label("in_transit"),
            ).where(NPCRow.content_id.in_(identities))
        )).all()
    }
    for known in rows:
        presence = npc_presence.get(known.knowledge_key)
        if presence is None:
            continue
        npc, in_transit = presence
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
        present = (
            npc.room_id == current_room_id
            and not in_transit
        )
        observed_availability = known.payload.get("availability", "unknown")
        availability = (
            "dead" if present and not npc.is_alive
            else "present" if present
            else observed_availability
            if observed_availability in {"away", "travelling", "dead"}
            else "unknown"
        )
        condition = (
            _condition_snapshot(npc)
            if present
            else known.payload.get("condition", {
                "kind": "unknown",
                "label": "Condition unknown",
            })
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
            "activity": (
                _activity_view(
                    goal,
                    availability,
                    current_room_id=current_room_id,
                    current_room_content_id=current_room_content_id,
                )
                if present else None
            ),
            "last_seen": {
                "room_name": known.payload.get(
                    "last_seen_room_name", known.place or "Unknown place"
                ),
                "at": _when(int(known.payload.get(
                    "last_seen_minute", known.learned_at_minute
                ))),
                "note": known.payload.get("last_seen_note"),
            },
            "condition": condition,
            "dialogue_topics": topics,
            "unread": (
                not suppress_unread
                and int(known.payload.get("revision", 1))
                > seen_revisions.get(known.knowledge_key, 0)
            ),
        })
    order = {"present": 0, "travelling": 1, "away": 2, "unknown": 3, "dead": 4}
    return sorted(result, key=lambda item: (order[item["availability"]], item["name"]))


def _condition_snapshot(npc: NPCRow) -> dict[str, str]:
    if not npc.is_alive or npc.hp <= 0:
        return {"kind": "dead", "label": "Known dead"}
    ratio = npc.hp / max(1, npc.max_hp)
    if ratio <= 0.3:
        return {"kind": "critical", "label": "Gravely wounded"}
    if ratio < 1:
        return {"kind": "wounded", "label": "Wounded"}
    return {"kind": "well", "label": "Appeared unhurt"}


def _condition_note(kind: str) -> str | None:
    return {
        "dead": "You saw them dead here.",
        "critical": "They were gravely wounded when last seen.",
        "wounded": "They were wounded when last seen.",
    }.get(kind)


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


def _activity_view(
    goal: NPCGoal | None,
    availability: str,
    *,
    current_room_id: int | None = None,
    current_room_content_id: str | None = None,
) -> dict:
    if availability == "dead":
        return {"kind": "unknown", "label": "No longer living"}
    if goal is None:
        return {"kind": "idle", "label": "Following their ordinary routine"}

    # A location-shaped private goal is stored as ``travel`` even when the
    # person is already standing at its target (Odran holding Gate Seven is
    # the sharpest example). Once deliberation has chosen a concrete current
    # intention, describe that observable intention instead of leaking—or
    # misrepresenting—the underlying private goal category.
    context = goal.context if isinstance(goal.context, dict) else {}
    current_intention = context.get("current_intention")
    authored = context.get("authored")
    location_goal_is_here = (
        isinstance(authored, dict)
        and authored.get("target_kind") == "location"
        and goal.target_id == current_room_content_id
    )
    if isinstance(current_intention, dict):
        target_room_id = current_intention.get("target_room_id")
        visibly_departing = (
            current_intention.get("kind") in {
                "travel",
                "seek_person",
                "avoid_person",
                "flee",
            }
            and isinstance(target_room_id, int)
            and target_room_id != current_room_id
        )
    else:
        visibly_departing = goal.kind == "travel" and not location_goal_is_here
    kind = "travelling" if visibly_departing else "working"
    return {
        "kind": kind,
        "label": (
            "Preparing to travel"
            if kind == "travelling"
            else "Occupied with private concerns"
        ),
    }


def _event_visible_to_player(
    event: WorldEvent,
    *,
    player_id: str,
    current_room_id: int,
) -> bool:
    """Apply the evidence boundary to one durable world event."""
    if player_id in (event.witnesses or []) or event.actor_id == player_id:
        return True
    # Legacy worlds may still hold old authored-conversation rows marked as
    # local aftermath. Spoken words leave no physical trace, so never reveal
    # them merely because a player later enters the same room.
    if event.kind == "authored_conversation":
        return False
    if event.visibility == "public":
        return True
    if event.visibility != "public_aftermath":
        return False
    # Public aftermath means the trace is locally discoverable, not that news
    # travels telepathically. Knowing a person must never reveal their distant
    # injury, disappearance, or death by itself; a witnessed/public event,
    # authored rumor, discoverable clue, or later visit must carry that truth.
    return event.room_id == current_room_id


def _event_provenance(
    event: WorldEvent,
    *,
    player_id: str,
    current_room_id: int,
    previously_learned: bool = False,
) -> str:
    if player_id in (event.witnesses or []) or event.actor_id == player_id:
        return "witnessed"
    if event.visibility == "public_aftermath" and (
        event.room_id == current_room_id or previously_learned
    ):
        return "found"
    return "heard"


def _visible_event_actor_ids(
    event: WorldEvent,
    *,
    player_id: str,
    known_person_ids: set[str],
) -> list[str]:
    # A footprint, empty chair, altered record, or other local aftermath does
    # not identify everyone who caused it. Linking known People cards here
    # would silently disclose private participants even when the authored
    # evidence deliberately leaves responsibility uncertain.
    witnessed = (
        player_id in (event.witnesses or [])
        or event.actor_id == player_id
    )
    if event.visibility != "public" and not witnessed:
        return []
    return [
        actor
        for actor in (event.actor_id, event.target_id)
        if actor == player_id or actor in known_person_ids
    ]


def _event_title(event: WorldEvent) -> str:
    return {
        "authored_story_turn": "A local trace",
        "player_npc_conversation": "Words exchanged",
        "situation_resolved": "A decisive moment",
        "npc_moved": "Someone moved on",
        "npc_relocated": "Someone moved on",
        "npc_disappeared": "Someone vanished",
        "rumor_shared": "A rumor changed hands",
        "npc_goal_changed": "A life changed direction",
        "missed_opportunity": "An opportunity passed",
    }.get(event.kind, event.kind.replace("_", " ").title())


async def _room_name(session: AsyncSession, room_id: int | None) -> str | None:
    if room_id is None:
        return None
    room = await session.get(Room, room_id)
    return room.name if room else None
