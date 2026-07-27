"""Runtime for authored causal windows and permanent missed opportunities."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Collection

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.living_world.clock import MINUTES_PER_DAY
from backend.living_world.memory import Memory
from backend.living_world import store
from backend.living_world_content import LivingWorldContent, load_living_world_content
from backend.models import (
    NPCMemory,
    NPCRow,
    TriggerFiring,
    WorldEvent,
    WorldFact,
    WorldState,
)

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TRIGGER_WATERMARK_KEY = "authored_triggers_through_minute"
_TRIGGER_DEFERRED_KEY = "authored_trigger_deferred_from"
_EFFECT_NPC_FIELDS = (
    "npc_id",
    "from_npc_id",
    "to_npc_id",
    "speaker_npc_id",
    "listener_npc_id",
)


@dataclass(frozen=True)
class TriggerAdvanceResult:
    fired: int = 0
    missed: int = 0
    effects_applied: int = 0


async def advance_authored_triggers(
    session: AsyncSession,
    *,
    from_minute: int,
    to_minute: int,
    active_room_ids: Collection[int],
    content: LivingWorldContent | None = None,
) -> TriggerAdvanceResult:
    """Resolve eligible authored triggers and commit one deterministic pass."""
    if from_minute < 0 or to_minute < 0:
        raise ValueError("trigger time cannot be negative")
    if to_minute < from_minute:
        raise ValueError("trigger time cannot move backwards")
    authored = content or load_living_world_content()
    active = frozenset(int(room_id) for room_id in active_room_ids)
    fired_count = missed_count = effects_count = 0
    try:
        state = await session.get(WorldState, 1)
        variables = dict(state.variables or {}) if state is not None else {}
        watermark = _nonnegative_minute(
            variables.get(_TRIGGER_WATERMARK_KEY),
            default=from_minute,
        )
        pass_from = min(from_minute, watermark)
        deferred = _deferred_trigger_minutes(
            variables.get(_TRIGGER_DEFERRED_KEY),
        )

        for trigger in authored.triggers.values():
            trigger_id = trigger["id"]
            trigger_from = min(
                pass_from,
                deferred.get(trigger_id, pass_from),
            )
            firings = (await session.execute(
                select(TriggerFiring).where(
                    TriggerFiring.trigger_id == trigger_id,
                    TriggerFiring.scope_id == "world",
                ).order_by(TriggerFiring.fired_at_minute, TriggerFiring.id)
            )).scalars().all()
            applied = [row for row in firings if row.outcome == "applied"]
            missed = any(row.outcome == "missed" for row in firings)
            window = trigger["window"]
            opens = int(window["opens_day"]) * MINUTES_PER_DAY
            closes = (
                (int(window["closes_day"]) + 1) * MINUTES_PER_DAY - 1
                if window["closes_day"] is not None else None
            )
            max_firings = int(window["max_firings"])

            if missed:
                deferred.pop(trigger_id, None)
                continue

            if len(applied) >= max_firings or to_minute < opens:
                deferred.pop(trigger_id, None)
                continue

            expired_unresolved = (
                closes is not None
                and to_minute > closes
                and not applied
            )
            candidate = max(opens, min(to_minute, closes or to_minute))
            if applied:
                cooldown = int(window["cooldown_minutes"])
                if candidate < applied[-1].fired_at_minute + cooldown:
                    deferred.pop(trigger_id, None)
                    continue

            participants = await _participants(
                session, trigger["participants"],
            )
            if len(participants) != len(trigger["participants"]):
                continue
            if await _touches_active_room(
                session,
                participants=participants,
                effects=(),
                active_room_ids=active,
            ):
                _defer_trigger(deferred, trigger_id, trigger_from)
                continue

            conditions_hold = (
                closes is None or trigger_from <= closes
            ) and await _conditions_hold(
                session,
                trigger=trigger,
                participants=participants,
                from_minute=max(trigger_from, opens),
                to_minute=candidate,
                content=authored,
            )
            if conditions_hold:
                if await _touches_active_room(
                    session,
                    participants=participants,
                    effects=trigger["effects"],
                    active_room_ids=active,
                ):
                    _defer_trigger(deferred, trigger_id, trigger_from)
                    continue
                deferred.pop(trigger_id, None)
                effects_count += await _record_applied(
                    session,
                    trigger=trigger,
                    participants=participants,
                    world_minute=candidate,
                    active_room_ids=active,
                    content=authored,
                    ordinal=len(applied) + 1,
                )
                fired_count += 1
                continue

            if not expired_unresolved:
                deferred.pop(trigger_id, None)
                continue

            # A coarse catch-up may cross a finite window in one pass. Its
            # closing conditions were evaluated above; only a failed branch
            # becomes a permanent missed opportunity.
            missed_effects = trigger.get("missed_consequences", [])
            if await _touches_active_room(
                session,
                participants=participants,
                effects=missed_effects,
                active_room_ids=active,
            ):
                _defer_trigger(deferred, trigger_id, trigger_from)
                continue
            deferred.pop(trigger_id, None)
            effects_count += await _record_missed(
                session,
                trigger=trigger,
                world_minute=closes,
                active_room_ids=active,
                content=authored,
            )
            missed_count += 1

        if state is not None:
            variables[_TRIGGER_WATERMARK_KEY] = max(watermark, to_minute)
            if deferred:
                variables[_TRIGGER_DEFERRED_KEY] = dict(sorted(deferred.items()))
            else:
                variables.pop(_TRIGGER_DEFERRED_KEY, None)
            if variables != (state.variables or {}):
                state.variables = variables
                state.revision += 1
        await session.commit()
        return TriggerAdvanceResult(
            fired=fired_count,
            missed=missed_count,
            effects_applied=effects_count,
        )
    except BaseException:
        await session.rollback()
        raise


def _nonnegative_minute(value, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return default
    return value


def _deferred_trigger_minutes(value) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        trigger_id: minute
        for trigger_id, raw_minute in value.items()
        if isinstance(trigger_id, str)
        and (
            minute := _nonnegative_minute(raw_minute, default=-1)
        ) >= 0
    }


def _defer_trigger(
    deferred: dict[str, int],
    trigger_id: str,
    from_minute: int,
) -> None:
    deferred[trigger_id] = min(
        from_minute,
        deferred.get(trigger_id, from_minute),
    )


async def _participants(session, ids) -> dict[str, NPCRow]:
    rows = (await session.execute(
        select(NPCRow).where(NPCRow.content_id.in_(tuple(ids)))
    )).scalars().all()
    return {str(row.content_id): row for row in rows}


async def _record_applied(
    session,
    *,
    trigger,
    participants,
    world_minute,
    active_room_ids,
    content,
    ordinal,
) -> int:
    effects = await _apply_effects(
        session,
        effects=trigger["effects"],
        trigger=trigger,
        world_minute=world_minute,
        active_room_ids=active_room_ids,
        content=content,
        outcome="applied",
    )
    event, _ = await store.chronicle_once(
        session,
        dedupe_key=f"trigger-event:{trigger['id']}:{ordinal}",
        kind=(
            "authored_conversation"
            if trigger["kind"] == "conversation"
            else "authored_story_turn"
        ),
        world_minute=world_minute,
        summary=_fired_summary(trigger),
        actor_id=trigger["participants"][0],
        target_id=(
            trigger["participants"][1]
            if len(trigger["participants"]) > 1 else None
        ),
        room_id=participants[trigger["participants"][0]].room_id,
        visibility="public_aftermath",
        payload={
            "trigger_id": trigger["id"],
            "opening_line": (
                trigger["conversation"]["opening_line"]
                if trigger.get("conversation") else None
            ),
        },
    )
    session.add(TriggerFiring(
        trigger_id=trigger["id"],
        scope_id="world",
        actor_npc_content_id=trigger["participants"][0],
        dedupe_key=f"trigger:{trigger['id']}:{ordinal}",
        ordinal=ordinal,
        fired_at_minute=world_minute,
        event_id=event.id,
        outcome="applied",
        evidence={"conditions": trigger["conditions"]},
    ))
    return effects


async def _record_missed(
    session,
    *,
    trigger,
    world_minute,
    active_room_ids,
    content,
) -> int:
    effects = trigger.get("missed_consequences", [])
    applied_effects = await _apply_effects(
        session,
        effects=effects,
        trigger=trigger,
        world_minute=world_minute,
        active_room_ids=active_room_ids,
        content=content,
        outcome="missed",
    )
    event, _ = await store.chronicle_once(
        session,
        dedupe_key=f"trigger-missed:{trigger['id']}",
        kind="missed_opportunity",
        world_minute=world_minute,
        summary=_missed_summary(trigger),
        visibility="public_aftermath",
        witnesses=(),
        payload={
            "trigger_id": trigger["id"],
            "aftermath_clues": trigger.get("aftermath_clues", []),
        },
    )
    session.add(TriggerFiring(
        trigger_id=trigger["id"],
        scope_id="world",
        dedupe_key=f"trigger:{trigger['id']}:missed",
        ordinal=1,
        fired_at_minute=world_minute,
        event_id=event.id,
        outcome="missed",
        evidence={"conditions_never_aligned": True},
    ))
    return applied_effects


async def _conditions_hold(
    session,
    *,
    trigger,
    participants,
    from_minute,
    to_minute,
    content,
) -> bool:
    location_rooms = await store.room_id_by_content(session)
    for condition in trigger["conditions"]:
        kind = condition["kind"]
        if kind == "co_located":
            rows = [participants[npc_id] for npc_id in condition["npc_ids"]]
            if len({row.room_id for row in rows}) != 1:
                return False
        elif kind == "npc_at":
            npc = participants.get(condition["npc_id"])
            if (
                npc is None
                or npc.room_id != location_rooms.get(condition["location_id"])
            ):
                return False
        elif kind == "day_phase":
            if not _interval_has_phase(
                from_minute, to_minute, set(condition["phases"])
            ):
                return False
        elif kind == "believes":
            rows = (await session.execute(
                select(NPCMemory).where(
                    NPCMemory.npc_content_id == condition["npc_id"]
                )
            )).scalars().all()
            minimum = float(condition["minimum_confidence"]) / 100.0
            if not any(
                row.payload.get("rumor_id") == condition["rumor_id"]
                and row.confidence >= minimum
                for row in rows
            ):
                return False
        elif kind == "trigger_fired":
            exists = (await session.execute(
                select(TriggerFiring.id).where(
                    TriggerFiring.trigger_id == condition["trigger_id"],
                    TriggerFiring.outcome == "applied",
                ).limit(1)
            )).first()
            if exists is None:
                return False
        elif kind == "route_pressure_at_least":
            passage = content.hostile_passages[
                condition["hostile_passage_id"]
            ]
            if passage["encounter_pressure"] < condition["minimum"]:
                return False
        elif kind == "carriage_arrives":
            carriage = content.carriages[condition["carriage_id"]]
            if condition["location_id"] not in carriage["stop_location_ids"]:
                return False
            if not _carriage_in_interval(
                carriage,
                content.routes,
                condition["location_id"],
                from_minute,
                to_minute,
            ):
                return False
        else:
            return False
    return True


def _interval_has_phase(start: int, end: int, phases: set[str]) -> bool:
    if end - start >= MINUTES_PER_DAY:
        return True
    for minute in range(start, end + 1, 30):
        if _authored_phase(minute) in phases:
            return True
    return _authored_phase(end) in phases


def _authored_phase(minute: int) -> str:
    local = minute % MINUTES_PER_DAY
    if local < 300:
        return "night"
    if local < 480:
        return "dawn"
    if local < 1080:
        return "day"
    if local < 1260:
        return "dusk"
    return "night"


def _carriage_in_interval(
    carriage: dict,
    routes,
    location_id: str,
    start: int,
    end: int,
) -> bool:
    """Whether this service is physically present at a stop in an interval."""
    if location_id not in carriage["stop_location_ids"]:
        return False
    trip_minutes = sum(
        int(routes[route_id]["travel_minutes"])
        for route_id in carriage["route_ids"]
    ) + max(0, len(carriage["route_ids"]) - 1) * int(
        carriage["layover_minutes"]
    )
    # An arrival just inside this interval may have departed a day or more
    # earlier, so search far enough back to cover the complete itinerary.
    first_day = max(0, (start - trip_minutes) // MINUTES_PER_DAY)
    last_day = max(first_day, end // MINUTES_PER_DAY)
    for day in range(first_day, last_day + 1):
        day_name = _DAYS[day % 7]
        for departure in carriage["departures"]:
            if departure["day"] != day_name:
                continue
            departure_minute = (
                day * MINUTES_PER_DAY + int(departure["minute"])
            )
            stop_times = _carriage_stop_times(
                carriage,
                routes,
                departure_location_id=departure["from_location_id"],
                departure_minute=departure_minute,
            )
            minute = stop_times.get(location_id)
            if minute is not None and start <= minute <= end:
                return True
    return False


def _carriage_stop_times(
    carriage: dict,
    routes,
    *,
    departure_location_id: str,
    departure_minute: int,
) -> dict[str, int]:
    stops = list(carriage["stop_location_ids"])
    route_ids = list(carriage["route_ids"])
    if departure_location_id == stops[0]:
        route_indexes = list(range(len(route_ids)))
        destination_indexes = list(range(1, len(stops)))
    elif departure_location_id == stops[-1]:
        route_indexes = list(reversed(range(len(route_ids))))
        destination_indexes = list(reversed(range(len(stops) - 1)))
    else:
        return {}

    result = {departure_location_id: departure_minute}
    minute = departure_minute
    for leg, (route_index, destination_index) in enumerate(
        zip(route_indexes, destination_indexes)
    ):
        minute += int(routes[route_ids[route_index]]["travel_minutes"])
        result[stops[destination_index]] = minute
        if leg < len(route_indexes) - 1:
            minute += int(carriage["layover_minutes"])
    return result


async def _touches_active_room(
    session,
    *,
    participants,
    effects,
    active_room_ids,
) -> bool:
    """Keep every DB-owned actor and movement target out of live rooms."""
    if not active_room_ids:
        return False
    if any(npc.room_id in active_room_ids for npc in participants.values()):
        return True

    npc_ids = {
        npc_id
        for effect in effects
        for field in _EFFECT_NPC_FIELDS
        if isinstance((npc_id := effect.get(field)), str)
    }
    if npc_ids:
        actor_rooms = (await session.execute(
            select(NPCRow.room_id).where(
                NPCRow.content_id.in_(tuple(sorted(npc_ids)))
            )
        )).scalars().all()
        if any(room_id in active_room_ids for room_id in actor_rooms):
            return True

    destinations = await _effect_destination_rooms(session, effects)
    return bool(destinations & active_room_ids)


async def _effect_destination_rooms(session, effects) -> set[int]:
    location_rooms = await store.room_id_by_content(session)
    return {
        location_rooms[location_id]
        for effect in effects
        for location_id in (
            effect.get("destination_location_id"),
            effect.get("location_id") if effect["kind"] == "set_direction" else None,
        )
        if location_id in location_rooms
    }


async def _apply_effects(
    session,
    *,
    effects,
    trigger,
    world_minute,
    active_room_ids,
    content,
    outcome,
) -> int:
    applied = 0
    for index, effect in enumerate(effects):
        kind = effect["kind"]
        key = f"{trigger['id']}:{outcome}:{world_minute}:{index}"
        if kind == "relationship_shift":
            await store.apply_social_delta(
                session,
                source_id=effect["from_npc_id"],
                target_id=effect["to_npc_id"],
                world_minute=world_minute,
                **{effect["axis"]: float(effect["delta"])},
            )
            applied += 1
        elif kind == "remember":
            memory = Memory(
                id=f"trigger-memory:{key}",
                owner_id=effect["npc_id"],
                kind="observation",
                summary=effect["summary"],
                tags=frozenset(effect["tags"]),
                importance=float(effect["importance"]),
                confidence=1.0,
                occurred_at=world_minute,
                shareable=True,
            )
            _row, inserted = await store.remember_once(session, memory)
            applied += int(inserted)
        elif kind == "share_rumor":
            applied += await _share_rumor(
                session,
                speaker_id=effect["speaker_npc_id"],
                listener_id=effect["listener_npc_id"],
                rumor_id=effect["rumor_id"],
                memory_key=f"trigger-rumor:{key}",
                world_minute=world_minute,
            )
        elif kind == "set_direction":
            await _set_direction(
                session,
                npc_id=effect["npc_id"],
                location_id=effect["location_id"],
                reason=effect["reason"],
                world_minute=world_minute,
                key=key,
            )
            applied += 1
        elif kind == "board_carriage":
            moved = await _board_carriage(
                session,
                npc_id=effect["npc_id"],
                location_id=effect["destination_location_id"],
                world_minute=world_minute,
                carriage_id=effect["carriage_id"],
                active_room_ids=active_room_ids,
            )
            applied += int(moved)
        elif kind == "leave_evidence":
            location_rooms = await store.room_id_by_content(session)
            room_id = location_rooms.get(effect["location_id"])
            await store.chronicle_once(
                session,
                dedupe_key=f"evidence:{key}",
                kind="evidence_left",
                world_minute=world_minute,
                summary=effect["description"],
                room_id=room_id,
                visibility="discoverable",
                payload={"location_id": effect["location_id"]},
            )
            applied += 1
        elif kind == "change_need":
            await store.ensure_fact(
                session,
                fact_key=f"need-pressure:{key}",
                subject_id=effect["npc_id"],
                predicate=f"need:{effect['need']}",
                value={"delta": effect["delta"], "source_trigger": trigger["id"]},
                confidence=1.0,
                visibility="hidden",
                world_minute=world_minute,
            )
            applied += 1
    return applied


async def _share_rumor(
    session,
    *,
    speaker_id,
    listener_id,
    rumor_id,
    memory_key,
    world_minute,
) -> int:
    rows = (await session.execute(
        select(NPCMemory).where(NPCMemory.npc_content_id == speaker_id)
    )).scalars().all()
    source = next(
        (row for row in rows if row.payload.get("rumor_id") == rumor_id),
        None,
    )
    if source is None:
        return 0
    received = Memory(
        id=memory_key,
        owner_id=listener_id,
        kind="rumour",
        summary=source.summary,
        tags=frozenset(source.tags or ()),
        importance=source.importance,
        confidence=max(0.1, source.confidence * 0.93),
        occurred_at=world_minute,
        source_id=speaker_id,
        source_memory_id=source.memory_key,
        shareable=source.shareable,
        secrecy=source.secrecy,
        cascade_depth=source.cascade_depth + 1,
    )
    _row, inserted = await store.remember_once(
        session,
        received,
        source_chain=[*(source.source_chain or []), speaker_id],
        payload={**(source.payload or {}), "transmitted_by_trigger": True},
    )
    return int(inserted)


async def _set_direction(
    session,
    *,
    npc_id,
    location_id,
    reason,
    world_minute,
    key,
) -> None:
    goal, _ = await store.ensure_goal(
        session,
        npc_content_id=npc_id,
        goal_key=f"trigger-direction:{key}",
        kind="travel",
        target_id=location_id,
        priority=100,
        next_deliberation_minute=world_minute,
        world_minute=world_minute,
        context={
            "desire": reason,
            "approach": "direct",
            "risk_tolerance": "bold",
            "target_kind": "location",
            "authored_trigger": True,
        },
    )
    goal.status = "active"
    goal.urgency = 100
    await store.schedule_once(
        session,
        dedupe_key=f"trigger-deliberate:{key}",
        kind="npc_deliberate",
        due_minute=world_minute,
        priority=5,
        actor_id=npc_id,
        payload={"purpose": "triggered_replan"},
    )


async def _board_carriage(
    session,
    *,
    npc_id,
    location_id,
    world_minute,
    carriage_id,
    active_room_ids,
) -> bool:
    npc = await store.npc_by_content_id(session, npc_id)
    rooms = await store.room_id_by_content(session)
    destination = rooms.get(location_id)
    if (
        npc is None
        or destination is None
        or npc.room_id in active_room_ids
        or destination in active_room_ids
    ):
        return False
    position = await store.arrival_position(
        session,
        room_id=destination,
        from_room_id=npc.room_id,
    )
    if position is None:
        return False
    origin = npc.room_id
    npc.room_id = destination
    npc.x, npc.y = position
    await store.chronicle_once(
        session,
        dedupe_key=f"carriage-board:{carriage_id}:{npc_id}:{world_minute}",
        kind="npc_boarded_carriage",
        world_minute=world_minute,
        summary=f"{npc.name} took the {carriage_id.replace('-', ' ')} east.",
        actor_id=npc_id,
        room_id=origin,
        visibility="public_aftermath",
        payload={"destination_room_id": destination},
    )
    return True


def _fired_summary(trigger: dict) -> str:
    if trigger.get("conversation"):
        return trigger["conversation"]["opening_line"]
    return f"The lives around {trigger['id'].replace('-', ' ')} changed course."


def _missed_summary(trigger: dict) -> str:
    clues = trigger.get("aftermath_clues") or ()
    if clues:
        return clues[0]["description"]
    return f"The window around {trigger['id'].replace('-', ' ')} closed."
