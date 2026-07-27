"""Transactional, deterministic simulation for NPC lives outside active rooms.

NPCs deliberate sparsely (three to six authored or stable fallback windows per
day).  A deliberation chooses only a general intention.  Travel, arrival,
conversation turns, and rumour transmission are then ordinary queued rules:
cheap, inspectable, restart-safe, and independent of language-model access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Any, Collection, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import RNG_SEED
from backend.living_world.clock import MINUTES_PER_DAY, compute_clock_advance
from backend.living_world.memory import (
    Memory,
    select_conversation_memories,
    transmit_rumour,
)
from backend.living_world.movement import RouteEdge, shortest_route
from backend.living_world.planner import (
    GoalCandidate,
    Intention,
    IntentionKind,
    choose_intention,
)
from backend.living_world.scheduler import next_deliberation
from backend.living_world import store
from backend.living_world_content import (
    LivingWorldContent,
    LivingWorldContentError,
    load_living_world_content,
)
from backend.models import NPCGoal, NPCMemory, NPCRow, Room, ScheduledWorldEvent


@dataclass(frozen=True)
class LivingWorldConfig:
    game_minutes_per_real_minute: float = 5.0
    catchup_cap_minutes: int = 8 * 60
    room_edge_travel_minutes: int = 10
    max_events_per_advance: int = 2_000
    max_conversations_per_advance: int = 32
    max_conversation_turns: int = 4
    max_rumour_cascade_depth: int = 3
    memories_per_conversation_turn: int = 1
    world_seed: int = RNG_SEED


DEFAULT_CONFIG = LivingWorldConfig()
_AUTHORED_TRIGGER_WATERMARK_KEY = "authored_triggers_through_minute"


@dataclass(frozen=True)
class AdvanceResult:
    from_minute: int
    to_minute: int
    simulated_minutes: int
    coalesced_minutes: int
    processed_events: int
    deliberations: int
    movements: int
    conversations: int
    memories_created: int
    skipped_active_events: int


@dataclass
class _Counters:
    processed_events: int = 0
    deliberations: int = 0
    movements: int = 0
    conversations: int = 0
    memories_created: int = 0
    skipped_active_events: int = 0
    writes: int = 0


_SUCCESSFUL_DEFAULT_CONTENT: LivingWorldContent | None = None


def _default_content() -> LivingWorldContent | None:
    """Load lazily; an in-progress/missing catalogue degrades to persona data."""
    global _SUCCESSFUL_DEFAULT_CONTENT
    if _SUCCESSFUL_DEFAULT_CONTENT is not None:
        return _SUCCESSFUL_DEFAULT_CONTENT
    try:
        _SUCCESSFUL_DEFAULT_CONTENT = load_living_world_content()
    except LivingWorldContentError:
        # The deterministic fallback still schedules every persisted NPC from
        # persona drives.  A malformed catalogue does not pause world time.
        return None
    return _SUCCESSFUL_DEFAULT_CONTENT


class LivingWorldService:
    def __init__(
        self,
        *,
        config: LivingWorldConfig = DEFAULT_CONFIG,
        content: LivingWorldContent | Any | None = None,
    ) -> None:
        self.config = config
        self.content = content if content is not None else _default_content()

    async def advance(
        self,
        session: AsyncSession,
        wall_now: float,
        active_room_ids: Collection[int],
    ) -> AdvanceResult:
        """Advance and commit one atomic slice of the dormant world.

        Repeating the same call after it commits is idempotent: the clock has
        consumed the same wall interval and every derived write has a stable
        deduplication key.  Active rooms are read only from this service; their
        in-memory runtime remains authoritative.
        """
        active_rooms = frozenset(int(room_id) for room_id in active_room_ids)
        counters = _Counters()
        try:
            state, created = await store.get_or_create_world_state(
                session,
                wall_now=wall_now,
                world_seed=self.config.world_seed,
            )
            counters.writes += int(created)
            from_minute = state.world_minute
            # Establish the trigger consumer's checkpoint before advancing
            # the producer clock. It remains unchanged until the separate
            # authored-trigger transaction succeeds, so a crash between the
            # two commits retries the exact interval. Existing saves begin at
            # their current minute rather than retro-evaluating ancient state.
            if _AUTHORED_TRIGGER_WATERMARK_KEY not in (state.variables or {}):
                variables = dict(state.variables or {})
                variables[_AUTHORED_TRIGGER_WATERMARK_KEY] = from_minute
                state.variables = variables
                counters.writes += 1

            last_real_at = state.last_real_at
            if isinstance(last_real_at, (datetime, float, int)):
                last_wall = store.datetime_epoch(last_real_at)
            else:
                # Defensive compatibility with an incomplete prototype row.
                last_wall = float(wall_now)
                state.last_real_at = store.epoch_datetime(last_wall)
                counters.writes += 1

            clock = compute_clock_advance(
                current_minute=from_minute,
                last_wall_at=last_wall,
                wall_now=float(wall_now),
                game_minutes_per_real_minute=(
                    self.config.game_minutes_per_real_minute
                ),
                catchup_cap_minutes=self.config.catchup_cap_minutes,
            )

            npcs = await store.living_npcs(session)
            sync_signature = self._sync_signature(npcs)
            if (state.variables or {}).get(
                "living_world_sync_signature"
            ) != sync_signature:
                counters.writes += await self._synchronize_people(
                    session, state.world_minute, npcs=npcs,
                )
                variables = dict(state.variables or {})
                variables["living_world_sync_signature"] = sync_signature
                state.variables = variables
                counters.writes += 1
            await self._drain_due_queue(
                session,
                through_minute=clock.to_minute,
                active_room_ids=active_rooms,
                counters=counters,
                world_seed=state.world_seed,
            )

            if clock.coalesced_minutes:
                _event, inserted = await store.chronicle_once(
                    session,
                    dedupe_key=(
                        f"quiet-interval:{from_minute}:{clock.to_minute}:"
                        f"{clock.coalesced_minutes}"
                    ),
                    kind="quiet_interval",
                    world_minute=clock.to_minute,
                    summary=(
                        "A long absence passed without resolving every "
                        "ordinary routine."
                    ),
                    visibility="developer",
                    payload={
                        "coalesced_minutes": clock.coalesced_minutes,
                        "catchup_cap_minutes": self.config.catchup_cap_minutes,
                    },
                )
                counters.writes += int(inserted)

            if clock.simulated_minutes or clock.coalesced_minutes:
                state.world_minute = clock.to_minute
                state.last_real_at = self._consumed_wall_time(
                    last_wall=last_wall,
                    wall_now=float(wall_now),
                    simulated_minutes=clock.simulated_minutes,
                    coalesced_minutes=clock.coalesced_minutes,
                )
                counters.writes += 1
            if counters.writes:
                variables = dict(state.variables or {})
                variables["living_world_schema"] = 1
                variables["last_coalesced_minutes"] = clock.coalesced_minutes
                state.variables = variables
                state.revision += 1

            await session.commit()
            return AdvanceResult(
                from_minute=from_minute,
                to_minute=clock.to_minute,
                simulated_minutes=clock.simulated_minutes,
                coalesced_minutes=clock.coalesced_minutes,
                processed_events=counters.processed_events,
                deliberations=counters.deliberations,
                movements=counters.movements,
                conversations=counters.conversations,
                memories_created=counters.memories_created,
                skipped_active_events=counters.skipped_active_events,
            )
        except BaseException:
            await session.rollback()
            raise

    def _consumed_wall_time(
        self,
        *,
        last_wall: float,
        wall_now: float,
        simulated_minutes: int,
        coalesced_minutes: int,
    ):
        if wall_now < last_wall:
            return store.epoch_datetime(last_wall)
        if coalesced_minutes:
            # The excess is intentionally represented by quiet_interval and
            # must not be replayed on the next process wake-up.
            return store.epoch_datetime(wall_now)
        consumed_seconds = (
            simulated_minutes
            * 60.0
            / self.config.game_minutes_per_real_minute
        )
        return store.epoch_datetime(last_wall + consumed_seconds)

    async def _synchronize_people(
        self,
        session: AsyncSession,
        world_minute: int,
        *,
        npcs: list[NPCRow],
    ) -> int:
        """Seed durable goals, beliefs, facts, and the next thought window."""
        writes = 0
        profiles = self._profiles

        for npc in npcs:
            assert npc.content_id is not None
            profile = profiles.get(npc.content_id)
            pending = await store.pending_actor_event(
                session,
                actor_id=npc.content_id,
                kinds=("npc_deliberate",),
            )
            due_minute = (
                pending.due_minute
                if pending is not None
                else self._next_deliberation(
                    profile, world_minute, npc.content_id,
                )
            )
            if pending is None:
                _row, inserted = await store.schedule_once(
                    session,
                    dedupe_key=f"deliberate:{npc.content_id}:{due_minute}",
                    kind="npc_deliberate",
                    due_minute=due_minute,
                    priority=50,
                    actor_id=npc.content_id,
                    room_id=npc.room_id,
                    payload={
                        "purpose": self._deliberation_purpose(
                            profile, due_minute,
                        ),
                    },
                )
                writes += int(inserted)

            definitions = self._goal_definitions(npc, profile)
            for definition in definitions:
                _goal, inserted = await store.ensure_goal(
                    session,
                    npc_content_id=npc.content_id,
                    goal_key=definition["id"],
                    kind=definition["kind"],
                    target_id=definition["target_id"],
                    priority=definition["priority"],
                    next_deliberation_minute=due_minute,
                    world_minute=world_minute,
                    context=definition["context"],
                )
                writes += int(inserted)

            writes += await self._seed_beliefs(
                session, npc.content_id,
            )

        for rumor_id, rumor in self._rumors.items():
            _fact, inserted = await store.ensure_fact(
                session,
                fact_key=f"rumor-truth:{rumor_id}",
                subject_id=rumor_id,
                predicate="underlying_account",
                value=dict(rumor.get("truth") or {}),
                confidence=1.0,
                visibility="hidden",
                world_minute=0,
            )
            writes += int(inserted)
        return writes

    def _sync_signature(self, npcs: list[NPCRow]) -> str:
        """Fingerprint story identities and authored seed definitions.

        The hot loop pays one compact NPC query, but avoids re-reading every
        goal and memory on each wake-up. A new/dead person or authored content
        revision changes the signature and runs the idempotent synchronizer.
        """
        parts: list[object] = [
            *(npc.content_id for npc in npcs),
            "--profiles--",
        ]
        for npc_id, profile in sorted(self._profiles.items()):
            parts.extend((
                npc_id,
                tuple(
                    (goal.get("id"), goal.get("priority"))
                    for goal in profile.get("private_goals", ())
                ),
                tuple(
                    window.get("minute")
                    for window in profile.get("deliberation_windows", ())
                ),
            ))
        parts.append("--rumors--")
        for rumor_id, rumor in sorted(self._rumors.items()):
            parts.extend((
                rumor_id,
                tuple(
                    belief.get("id")
                    for belief in rumor.get("beliefs", ())
                ),
            ))
        return hashlib.blake2b(
            "\x1f".join(repr(part) for part in parts).encode("utf-8"),
            digest_size=16,
        ).hexdigest()

    @property
    def _profiles(self) -> Mapping[str, dict[str, Any]]:
        value = getattr(self.content, "npc_profiles", {}) if self.content else {}
        return value if isinstance(value, Mapping) else {}

    @property
    def _rumors(self) -> Mapping[str, dict[str, Any]]:
        value = getattr(self.content, "rumors", {}) if self.content else {}
        return value if isinstance(value, Mapping) else {}

    def _goal_definitions(
        self,
        npc: NPCRow,
        profile: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if profile is not None:
            result = []
            for goal in profile.get("private_goals", ()):
                target = goal.get("target") or {}
                target_kind = str(target.get("kind", "self"))
                result.append({
                    "id": str(goal["id"]),
                    "kind": self._goal_intention_kind(target_kind).value,
                    "target_id": str(target.get("id")) if target.get("id") else None,
                    "priority": float(goal.get("priority", 1)) * 20.0,
                    "context": {
                        "desire": str(goal.get("desire", "")),
                        "approach": str(goal.get("approach", "patient")),
                        "risk_tolerance": str(
                            goal.get("risk_tolerance", "measured")
                        ),
                        "target_kind": target_kind,
                    },
                })
            if result:
                return result

        drives = (
            npc.persona.get("drives", ())
            if isinstance(npc.persona, dict)
            else ()
        )
        return [
            {
                "id": f"drive:{index}",
                "kind": IntentionKind.WORK.value,
                "target_id": npc.content_id,
                "priority": max(20.0, 60.0 - index * 5.0),
                "context": {
                    "desire": str(drive),
                    "approach": "patient",
                    "risk_tolerance": "measured",
                    "target_kind": "self",
                },
            }
            for index, drive in enumerate(drives)
        ] or [{
            "id": "continue-living",
            "kind": IntentionKind.KEEP_SCHEDULE.value,
            "target_id": npc.content_id,
            "priority": 40.0,
            "context": {
                "desire": "Continue the ordinary shape of the day.",
                "approach": "patient",
                "risk_tolerance": "measured",
                "target_kind": "self",
            },
        }]

    @staticmethod
    def _goal_intention_kind(target_kind: str) -> IntentionKind:
        return {
            "location": IntentionKind.TRAVEL,
            "kingdom": IntentionKind.TRAVEL,
            "npc": IntentionKind.SEEK_PERSON,
            "rumor": IntentionKind.INVESTIGATE,
            "self": IntentionKind.GUARD,
        }.get(target_kind, IntentionKind.INVESTIGATE)

    async def _seed_beliefs(
        self,
        session: AsyncSession,
        npc_content_id: str,
    ) -> int:
        writes = 0
        for rumor_id, rumor in self._rumors.items():
            transmission = rumor.get("transmission") or {}
            threshold = float(transmission.get("share_threshold", 0))
            for belief in rumor.get("beliefs", ()):
                if belief.get("holder_npc_id") != npc_content_id:
                    continue
                source = belief.get("source") or {}
                confidence = max(
                    0.0, min(1.0, float(belief.get("confidence", 0)) / 100.0)
                )
                memory = Memory(
                    id=f"belief:{belief['id']}",
                    owner_id=npc_content_id,
                    kind="rumour",
                    summary=str(belief.get("claim", rumor.get("topic", ""))),
                    tags=self._rumor_tags(rumor_id, rumor),
                    importance=5.0,
                    confidence=confidence,
                    occurred_at=0,
                    source_id=(
                        str(source.get("ref"))
                        if source.get("kind") == "npc"
                        else None
                    ),
                    shareable=float(belief.get("confidence", 0)) >= threshold,
                    secrecy=0.0,
                    cascade_depth=len(source.get("chain") or ()),
                )
                _row, inserted = await store.remember_once(
                    session,
                    memory,
                    source_chain=source.get("chain") or (),
                    payload={
                        "rumor_id": rumor_id,
                        "belief_id": belief.get("id"),
                        "truth_alignment": belief.get("truth_alignment"),
                        "source_kind": source.get("kind"),
                        "root_memory_id": memory.id,
                    },
                )
                writes += int(inserted)
        return writes

    @staticmethod
    def _rumor_tags(
        rumor_id: str,
        rumor: Mapping[str, Any],
    ) -> frozenset[str]:
        tokens = {
            token
            for token in rumor_id.replace("_", "-").split("-")
            if len(token) > 2
        }
        topic = str(rumor.get("topic", "")).lower()
        for token in ("rot", "road", "carriage", "medicine", "names", "kingdom"):
            if token in topic:
                tokens.add(token)
        return frozenset({rumor_id, "rumour", *tokens})

    def _next_deliberation(
        self,
        profile: Mapping[str, Any] | None,
        after_minute: int,
        npc_content_id: str,
    ) -> int:
        windows = tuple(
            sorted(
                int(window["minute"])
                for window in (profile or {}).get("deliberation_windows", ())
            )
        )
        if not windows:
            # The stable scheduler produces exactly 3..6 windows a day.
            profile_id = str((profile or {}).get("id", npc_content_id))
            return next_deliberation(profile_id, after_minute)
        day = max(0, after_minute) // MINUTES_PER_DAY
        while True:
            for local_minute in windows:
                candidate = day * MINUTES_PER_DAY + local_minute
                if candidate > after_minute:
                    return candidate
            day += 1

    @staticmethod
    def _deliberation_purpose(
        profile: Mapping[str, Any] | None,
        due_minute: int,
    ) -> str:
        local = due_minute % MINUTES_PER_DAY
        for window in (profile or {}).get("deliberation_windows", ()):
            if int(window["minute"]) == local:
                return str(window.get("purpose", "replan"))
        return "replan"

    async def _drain_due_queue(
        self,
        session: AsyncSession,
        *,
        through_minute: int,
        active_room_ids: frozenset[int],
        counters: _Counters,
        world_seed: int,
    ) -> None:
        excluded: set[int] = set()
        routes = await store.route_edges(
            session,
            travel_minutes=self.config.room_edge_travel_minutes,
        )
        room_ids = await store.room_id_by_content(session)
        while counters.processed_events < self.config.max_events_per_advance:
            event = await store.next_due_event(
                session,
                through_minute=through_minute,
                excluded_ids=excluded,
            )
            if event is None:
                break

            actor = (
                await store.npc_by_content_id(session, event.actor_id)
                if event.actor_id
                else None
            )
            if actor is not None and actor.room_id in active_room_ids:
                excluded.add(event.id)
                counters.skipped_active_events += 1
                continue
            if self._event_destination_is_active(event, active_room_ids):
                excluded.add(event.id)
                counters.skipped_active_events += 1
                continue

            if event.kind == "npc_deliberate":
                await self._resolve_deliberation(
                    session,
                    event=event,
                    actor=actor,
                    room_ids=room_ids,
                    routes=routes,
                    counters=counters,
                )
            elif event.kind == "npc_arrive_room":
                await self._resolve_arrival(
                    session,
                    event=event,
                    actor=actor,
                    counters=counters,
                )
            elif event.kind == "npc_conversation":
                if (
                    counters.conversations
                    >= self.config.max_conversations_per_advance
                ):
                    event.attempt_count += 1
                    store.cancel_scheduled_event(
                        event,
                        world_minute=event.due_minute,
                        reason="conversation budget exhausted",
                    )
                    counters.processed_events += 1
                    counters.writes += 1
                    continue
                resolved = await self._resolve_conversation(
                    session,
                    event=event,
                    actor=actor,
                    active_room_ids=active_room_ids,
                    counters=counters,
                    world_seed=world_seed,
                )
                if not resolved:
                    excluded.add(event.id)
                    counters.skipped_active_events += 1
                    continue
            else:
                event.attempt_count += 1
                store.cancel_scheduled_event(
                    event,
                    world_minute=event.due_minute,
                    reason=f"unsupported living-world event {event.kind!r}",
                )
                counters.processed_events += 1
                counters.writes += 1

    @staticmethod
    def _event_destination_is_active(
        event: ScheduledWorldEvent,
        active_room_ids: frozenset[int],
    ) -> bool:
        if event.kind != "npc_arrive_room":
            return False
        destination = (event.payload or {}).get("to_room_id")
        return isinstance(destination, int) and destination in active_room_ids

    async def _resolve_deliberation(
        self,
        session: AsyncSession,
        *,
        event: ScheduledWorldEvent,
        actor: NPCRow | None,
        room_ids: Mapping[str, int],
        routes: list[RouteEdge],
        counters: _Counters,
    ) -> None:
        event.attempt_count += 1
        if actor is None or not actor.is_alive or not actor.content_id:
            store.cancel_scheduled_event(
                event,
                world_minute=event.due_minute,
                reason="NPC no longer exists or is dead",
            )
            counters.processed_events += 1
            counters.writes += 1
            return

        profile = self._profiles.get(actor.content_id)
        goals = await store.goals_for_npc(session, actor.content_id)
        next_due = self._next_deliberation(
            profile, event.due_minute, actor.content_id,
        )
        _next, inserted = await store.schedule_once(
            session,
            dedupe_key=f"deliberate:{actor.content_id}:{next_due}",
            kind="npc_deliberate",
            due_minute=next_due,
            priority=50,
            actor_id=actor.content_id,
            room_id=actor.room_id,
            payload={
                "purpose": self._deliberation_purpose(profile, next_due),
            },
        )
        counters.writes += int(inserted)

        candidates, target_rooms = await self._goal_candidates(
            session,
            actor=actor,
            profile=profile,
            goals=goals,
            room_ids=room_ids,
            routes=routes,
            at_minute=event.due_minute,
        )
        chosen = choose_intention(candidates)
        for goal in goals:
            goal.next_deliberation_minute = next_due
        selected_goal = (
            next(
                (goal for goal in goals if chosen and goal.goal_key == chosen.goal_key),
                None,
            )
        )
        if selected_goal is not None and chosen is not None:
            selected_goal.last_deliberated_minute = event.due_minute
            context = dict(selected_goal.context or {})
            context["current_intention"] = {
                "kind": chosen.kind.value,
                "utility": chosen.utility,
                "chosen_at_minute": event.due_minute,
                "target_room_id": target_rooms.get(chosen.goal_key),
            }
            selected_goal.context = context

        intention_name = (
            chosen.kind.value.replace("_", " ") if chosen is not None else "wait"
        )
        _chronicle, inserted = await store.chronicle_once(
            session,
            dedupe_key=f"deliberation:{actor.content_id}:{event.due_minute}",
            kind="npc_deliberated",
            world_minute=event.due_minute,
            actor_id=actor.content_id,
            room_id=actor.room_id,
            summary=f"{actor.name} settled on a direction: {intention_name}.",
            visibility="developer",
            payload={
                "goal_key": chosen.goal_key if chosen else None,
                "intention": chosen.kind.value if chosen else "wait",
                "utility": chosen.utility if chosen else 0,
                "purpose": (event.payload or {}).get("purpose"),
            },
        )
        counters.writes += int(inserted)

        if chosen is not None:
            destination = target_rooms.get(chosen.goal_key)
            can_relocate = bool(
                (profile or {}).get("offscreen_policy", {}).get(
                    "can_relocate", True,
                )
            )
            if (
                can_relocate
                and destination is not None
                and destination != actor.room_id
            ):
                await self._begin_travel(
                    session,
                    actor=actor,
                    chosen=chosen,
                    selected_goal=selected_goal,
                    destination=destination,
                    routes=routes,
                    world_minute=event.due_minute,
                    counters=counters,
                )

        counters.writes += await self._schedule_conversation(
            session,
            actor=actor,
            world_minute=event.due_minute,
        )
        store.resolve_scheduled_event(
            event, world_minute=event.due_minute,
        )
        counters.deliberations += 1
        counters.processed_events += 1
        counters.writes += 1

    async def _goal_candidates(
        self,
        session: AsyncSession,
        *,
        actor: NPCRow,
        profile: Mapping[str, Any] | None,
        goals: list[NPCGoal],
        room_ids: Mapping[str, int],
        routes: list[RouteEdge],
        at_minute: int,
    ) -> tuple[list[GoalCandidate], dict[str, int | None]]:
        candidates: list[GoalCandidate] = []
        target_rooms: dict[str, int | None] = {}

        schedule = self._schedule_anchor(profile, at_minute)
        if schedule is not None:
            key = (
                f"schedule:{schedule.get('start_minute')}:"
                f"{schedule.get('location_id')}"
            )
            target_room = room_ids.get(str(schedule.get("location_id")))
            candidates.append(GoalCandidate(
                key=key,
                intention=IntentionKind.KEEP_SCHEDULE,
                base_priority=float(schedule.get("commitment", 50)),
                target_room_id=target_room,
                commitment_cost=0,
                metadata={
                    "activity": schedule.get("activity"),
                    "authored_schedule": True,
                },
            ))
            target_rooms[key] = target_room

        for goal in goals:
            if goal.status in {"completed", "failed", "abandoned"}:
                continue
            authored = (goal.context or {}).get("authored") or {}
            target_kind = authored.get("target_kind")
            target_room: int | None = None
            if target_kind == "location":
                target_room = room_ids.get(goal.target_id or "")
            elif target_kind == "npc" and goal.target_id:
                target_npc = await store.npc_by_content_id(
                    session, goal.target_id,
                )
                target_room = target_npc.room_id if target_npc else None

            travel_cost = 0.0
            if target_room is not None and target_room != actor.room_id:
                route = shortest_route(
                    routes,
                    from_room_id=actor.room_id,
                    to_room_id=target_room,
                )
                travel_cost = (
                    route.travel_minutes / 10.0 if route is not None else 1_000.0
                )
            try:
                intention = IntentionKind(goal.kind)
            except ValueError:
                intention = IntentionKind.INVESTIGATE
            deadline_urgency = float(goal.urgency)
            if goal.deadline_minute is not None:
                remaining = max(0, goal.deadline_minute - at_minute)
                deadline_urgency += max(0.0, 30.0 - remaining / 30.0)
            candidates.append(GoalCandidate(
                key=goal.goal_key,
                intention=intention,
                base_priority=float(goal.priority),
                target_id=goal.target_id,
                target_room_id=target_room,
                deadline_urgency=deadline_urgency,
                travel_cost=travel_cost,
                metadata={"private_goal": True},
            ))
            target_rooms[goal.goal_key] = target_room
        return candidates, target_rooms

    @staticmethod
    def _schedule_anchor(
        profile: Mapping[str, Any] | None,
        world_minute: int,
    ) -> Mapping[str, Any] | None:
        schedule = tuple((profile or {}).get("schedule", ()))
        if not schedule:
            return None
        local = world_minute % MINUTES_PER_DAY
        eligible = [
            anchor
            for anchor in schedule
            if int(anchor.get("start_minute", 0)) <= local
        ]
        return eligible[-1] if eligible else schedule[-1]

    async def _begin_travel(
        self,
        session: AsyncSession,
        *,
        actor: NPCRow,
        chosen: Intention,
        selected_goal: NPCGoal | None,
        destination: int,
        routes: list[RouteEdge],
        world_minute: int,
        counters: _Counters,
    ) -> bool:
        assert actor.content_id is not None
        if await store.has_pending_actor_action(
            session,
            actor_id=actor.content_id,
            kinds=("npc_arrive_room",),
        ):
            return False
        route = shortest_route(
            routes,
            from_room_id=actor.room_id,
            to_room_id=destination,
        )
        if route is None or not route.edges:
            if selected_goal is not None:
                selected_goal.status = "blocked"
                selected_goal.failure_reason = "no passable room route"
            _event, inserted = await store.chronicle_once(
                session,
                dedupe_key=(
                    f"travel-blocked:{actor.content_id}:"
                    f"{world_minute}:{destination}"
                ),
                kind="npc_travel_blocked",
                world_minute=world_minute,
                actor_id=actor.content_id,
                room_id=actor.room_id,
                summary=f"{actor.name} found no passable road onward.",
                visibility="developer",
                payload={"destination_room_id": destination},
            )
            counters.writes += int(inserted)
            return False

        journey_key = (
            f"journey:{actor.content_id}:{world_minute}:"
            f"{actor.room_id}:{destination}"
        )
        first_edge = route.edges[0]
        _scheduled, inserted = await store.schedule_once(
            session,
            dedupe_key=f"{journey_key}:step:1",
            kind="npc_arrive_room",
            due_minute=world_minute + first_edge.travel_minutes,
            priority=10,
            actor_id=actor.content_id,
            target_id=str(first_edge.to_room_id),
            room_id=first_edge.from_room_id,
            payload={
                "journey_key": journey_key,
                "goal_key": chosen.goal_key,
                "route_room_ids": list(route.room_ids),
                "step_index": 1,
                "from_room_id": first_edge.from_room_id,
                "to_room_id": first_edge.to_room_id,
                "final_room_id": destination,
                "travel_minutes": first_edge.travel_minutes,
            },
        )
        counters.writes += int(inserted)
        if selected_goal is not None:
            selected_goal.plan_steps = [
                {
                    "action": "travel",
                    "from_room_id": edge.from_room_id,
                    "to_room_id": edge.to_room_id,
                    "travel_minutes": edge.travel_minutes,
                }
                for edge in route.edges
            ]
            selected_goal.current_step = 0
            selected_goal.failure_reason = None
            if selected_goal.status == "blocked":
                selected_goal.status = "active"
        _event, inserted_event = await store.chronicle_once(
            session,
            dedupe_key=f"chronicle:{journey_key}:began",
            kind="npc_began_travel",
            world_minute=world_minute,
            actor_id=actor.content_id,
            target_id=str(destination),
            room_id=actor.room_id,
            summary=f"{actor.name} set out along the road.",
            visibility="witnessed",
            witnesses=[
                row.content_id
                for row in await store.roommates(
                    session,
                    room_id=actor.room_id,
                    excluding_id=actor.content_id,
                )
                if row.content_id
            ],
            payload={
                "journey_key": journey_key,
                "destination_room_id": destination,
                "route_room_ids": list(route.room_ids),
            },
        )
        counters.writes += int(inserted_event)
        plan_memory = Memory(
            id=f"plan:{journey_key}",
            owner_id=actor.content_id,
            kind="plan",
            summary=f"{actor.name} decided to travel onward.",
            tags=frozenset({"travel", "road"}),
            importance=3.0,
            confidence=1.0,
            occurred_at=world_minute,
            shareable=False,
        )
        _memory, memory_inserted = await store.remember_once(
            session,
            plan_memory,
            source_event_id=_event.id,
            payload={
                "journey_key": journey_key,
                "destination_room_id": destination,
            },
        )
        counters.memories_created += int(memory_inserted)
        counters.writes += int(memory_inserted)
        return inserted

    async def _resolve_arrival(
        self,
        session: AsyncSession,
        *,
        event: ScheduledWorldEvent,
        actor: NPCRow | None,
        counters: _Counters,
    ) -> None:
        event.attempt_count += 1
        payload = dict(event.payload or {})
        if actor is None or not actor.is_alive or not actor.content_id:
            store.cancel_scheduled_event(
                event,
                world_minute=event.due_minute,
                reason="traveller no longer exists or is dead",
            )
            counters.processed_events += 1
            counters.writes += 1
            return
        from_room = payload.get("from_room_id")
        to_room = payload.get("to_room_id")
        path = payload.get("route_room_ids")
        step_index = payload.get("step_index")
        if (
            not isinstance(from_room, int)
            or not isinstance(to_room, int)
            or not isinstance(path, list)
            or not isinstance(step_index, int)
            or actor.room_id != from_room
            or not await store.connection_exists(
                session,
                from_room_id=from_room,
                to_room_id=to_room,
            )
        ):
            store.cancel_scheduled_event(
                event,
                world_minute=event.due_minute,
                reason="journey route changed before arrival",
            )
            await self._record_failed_journey(
                session, event=event, actor=actor, counters=counters,
            )
            counters.processed_events += 1
            counters.writes += 1
            return
        position = await store.arrival_position(
            session,
            room_id=to_room,
            from_room_id=from_room,
        )
        if position is None:
            store.cancel_scheduled_event(
                event,
                world_minute=event.due_minute,
                reason="destination has no free floor tile",
            )
            await self._record_failed_journey(
                session, event=event, actor=actor, counters=counters,
            )
            counters.processed_events += 1
            counters.writes += 1
            return

        actor.room_id = to_room
        actor.x, actor.y = position
        destination = await session.get(Room, to_room)
        witnesses = [
            row.content_id
            for row in await store.roommates(
                session,
                room_id=to_room,
                excluding_id=actor.content_id,
            )
            if row.content_id
        ]
        chronicle, inserted = await store.chronicle_once(
            session,
            dedupe_key=f"chronicle:{event.dedupe_key}:arrived",
            kind="npc_arrived_room",
            world_minute=event.due_minute,
            actor_id=actor.content_id,
            target_id=str(to_room),
            room_id=to_room,
            summary=(
                f"{actor.name} arrived at "
                f"{destination.name if destination else 'the next road'}."
            ),
            visibility="witnessed",
            witnesses=witnesses,
            payload={
                "journey_key": payload.get("journey_key"),
                "from_room_id": from_room,
                "to_room_id": to_room,
                "step_index": step_index,
            },
        )
        counters.writes += int(inserted)
        memory = Memory(
            id=f"outcome:{event.dedupe_key}",
            owner_id=actor.content_id,
            kind="outcome",
            summary=(
                f"{actor.name} reached "
                f"{destination.name if destination else 'the next road'}."
            ),
            tags=frozenset({"travel", "arrival"}),
            importance=3.0,
            confidence=1.0,
            occurred_at=event.due_minute,
            shareable=True,
        )
        _memory, memory_inserted = await store.remember_once(
            session,
            memory,
            source_event_id=chronicle.id,
            payload={
                "journey_key": payload.get("journey_key"),
                "room_id": to_room,
            },
        )
        counters.memories_created += int(memory_inserted)
        counters.writes += int(memory_inserted)

        goal_key = payload.get("goal_key")
        if isinstance(goal_key, str):
            goal = next(
                (
                    row
                    for row in await store.goals_for_npc(
                        session, actor.content_id,
                    )
                    if row.goal_key == goal_key
                ),
                None,
            )
            if goal is not None:
                goal.current_step = step_index
                context = dict(goal.context or {})
                context["last_arrival"] = {
                    "room_id": to_room,
                    "world_minute": event.due_minute,
                }
                goal.context = context

        if step_index < len(path) - 1:
            next_room = path[step_index + 1]
            travel_minutes = int(
                payload.get(
                    "travel_minutes",
                    self.config.room_edge_travel_minutes,
                )
            )
            journey_key = str(payload.get("journey_key"))
            _next, next_inserted = await store.schedule_once(
                session,
                dedupe_key=f"{journey_key}:step:{step_index + 1}",
                kind="npc_arrive_room",
                due_minute=event.due_minute + travel_minutes,
                priority=10,
                actor_id=actor.content_id,
                target_id=str(next_room),
                room_id=to_room,
                payload={
                    **payload,
                    "step_index": step_index + 1,
                    "from_room_id": to_room,
                    "to_room_id": next_room,
                },
            )
            counters.writes += int(next_inserted)
        store.resolve_scheduled_event(
            event, world_minute=event.due_minute,
        )
        counters.movements += 1
        counters.processed_events += 1
        counters.writes += 1

    async def _record_failed_journey(
        self,
        session: AsyncSession,
        *,
        event: ScheduledWorldEvent,
        actor: NPCRow,
        counters: _Counters,
    ) -> None:
        _chronicle, inserted = await store.chronicle_once(
            session,
            dedupe_key=f"chronicle:{event.dedupe_key}:failed",
            kind="npc_travel_failed",
            world_minute=event.due_minute,
            actor_id=actor.content_id,
            room_id=actor.room_id,
            summary=f"{actor.name}'s route failed beneath their feet.",
            visibility="developer",
            payload={
                "reason": event.last_error,
                "journey_key": (event.payload or {}).get("journey_key"),
            },
        )
        counters.writes += int(inserted)

    async def _schedule_conversation(
        self,
        session: AsyncSession,
        *,
        actor: NPCRow,
        world_minute: int,
    ) -> int:
        assert actor.content_id is not None
        memories = [
            row
            for row in await store.memory_rows(
                session, actor.content_id, now_minute=world_minute,
            )
            if row.shareable
            and row.cascade_depth < self.config.max_rumour_cascade_depth
        ]
        listeners = await store.roommates(
            session,
            room_id=actor.room_id,
            excluding_id=actor.content_id,
        )
        if not memories or not listeners:
            return 0
        listener = listeners[
            _stable_u64(actor.content_id, world_minute, "listener")
            % len(listeners)
        ]
        if listener.content_id is None:
            return 0
        root_key = (
            f"conversation:{actor.room_id}:{actor.content_id}:"
            f"{listener.content_id}:{world_minute}"
        )
        topic_tags = sorted(
            set().union(*(set(memory.tags or ()) for memory in memories))
        )
        _event, inserted = await store.schedule_once(
            session,
            dedupe_key=f"{root_key}:turn:1",
            kind="npc_conversation",
            due_minute=world_minute,
            priority=70,
            actor_id=actor.content_id,
            target_id=listener.content_id,
            room_id=actor.room_id,
            payload={
                "root_key": root_key,
                "turn": 1,
                "remaining_turns": self.config.max_conversation_turns,
                "topic_tags": topic_tags,
            },
        )
        return int(inserted)

    async def _resolve_conversation(
        self,
        session: AsyncSession,
        *,
        event: ScheduledWorldEvent,
        actor: NPCRow | None,
        active_room_ids: frozenset[int],
        counters: _Counters,
        world_seed: int,
    ) -> bool:
        listener = (
            await store.npc_by_content_id(session, event.target_id)
            if event.target_id
            else None
        )
        if (
            actor is not None
            and listener is not None
            and (
                actor.room_id in active_room_ids
                or listener.room_id in active_room_ids
            )
        ):
            return False

        event.attempt_count += 1
        if (
            actor is None
            or listener is None
            or not actor.is_alive
            or not listener.is_alive
            or not actor.content_id
            or not listener.content_id
            or actor.room_id != listener.room_id
        ):
            store.cancel_scheduled_event(
                event,
                world_minute=event.due_minute,
                reason="speakers are no longer together",
            )
            counters.processed_events += 1
            counters.writes += 1
            return True

        speaker_rows = [
            row
            for row in await store.memory_rows(
                session, actor.content_id, now_minute=event.due_minute,
            )
            if row.shareable
            and row.cascade_depth < self.config.max_rumour_cascade_depth
        ]
        listener_rows = await store.memory_rows(
            session, listener.content_id, now_minute=event.due_minute,
        )
        known_rumors = {
            (row.payload or {}).get("rumor_id")
            for row in listener_rows
            if (row.payload or {}).get("rumor_id")
        }
        candidates = [
            row
            for row in speaker_rows
            if not (
                (row.payload or {}).get("rumor_id")
                and (row.payload or {}).get("rumor_id") in known_rumors
            )
            and row.memory_key not in {
                listener_row.source_memory_id for listener_row in listener_rows
            }
        ]
        topic_tags = frozenset(
            str(tag)
            for tag in (event.payload or {}).get("topic_tags", ())
        )
        trust = await store.relationship_trust(
            session,
            source_id=actor.content_id,
            target_id=listener.content_id,
        )
        selected = select_conversation_memories(
            (store.memory_from_row(row) for row in candidates),
            topic_tags=topic_tags,
            now_minute=event.due_minute,
            trust=trust,
            max_items=self.config.memories_per_conversation_turn,
            max_cascade_depth=self.config.max_rumour_cascade_depth,
        )
        source_by_key = {row.memory_key: row for row in candidates}

        transmitted = False
        if selected:
            source_memory = selected[0]
            source_row = source_by_key[source_memory.id]
            source_row.last_recalled_minute = event.due_minute
            precision = 0.65 + 0.35 * _stable_unit(
                world_seed,
                actor.content_id,
                listener.content_id,
                source_memory.id,
                event.due_minute,
            )
            received = transmit_rumour(
                source_memory,
                receiver_id=listener.content_id,
                speaker_id=actor.content_id,
                world_minute=event.due_minute,
                precision=precision,
            )
            witnesses = [
                row.content_id
                for row in await store.roommates(
                    session,
                    room_id=actor.room_id,
                )
                if row.content_id
            ]
            chronicle, inserted = await store.chronicle_once(
                session,
                dedupe_key=f"chronicle:{event.dedupe_key}",
                kind="npc_shared_rumour",
                world_minute=event.due_minute,
                actor_id=actor.content_id,
                target_id=listener.content_id,
                room_id=actor.room_id,
                summary=(
                    f"{actor.name} shared a piece of road-talk with "
                    f"{listener.name}."
                ),
                visibility="witnessed",
                witnesses=witnesses,
                payload={
                    "source_memory_id": source_memory.id,
                    "rumor_id": (source_row.payload or {}).get("rumor_id"),
                    "precision": precision,
                    "turn": (event.payload or {}).get("turn", 1),
                },
            )
            counters.writes += int(inserted)
            source_chain = list(source_row.source_chain or ())
            if not source_chain or source_chain[-1] != actor.content_id:
                source_chain.append(actor.content_id)
            root_memory_id = (
                (source_row.payload or {}).get("root_memory_id")
                or source_memory.id
            )
            payload = dict(source_row.payload or {})
            payload.update({
                "root_memory_id": root_memory_id,
                "received_in_conversation": event.dedupe_key,
                "precision": precision,
            })
            _row, memory_inserted = await store.remember_once(
                session,
                received,
                source_chain=source_chain,
                source_event_id=chronicle.id,
                payload=payload,
            )
            transmitted = memory_inserted
            counters.memories_created += int(memory_inserted)
            counters.writes += int(memory_inserted)
            await store.apply_social_delta(
                session,
                source_id=listener.content_id,
                target_id=actor.content_id,
                world_minute=event.due_minute,
                familiarity=1.0,
                trust=0.25,
            )
            await store.apply_social_delta(
                session,
                source_id=actor.content_id,
                target_id=listener.content_id,
                world_minute=event.due_minute,
                familiarity=0.5,
            )
            counters.writes += 1

        payload = dict(event.payload or {})
        remaining = int(payload.get("remaining_turns", 1))
        turn = int(payload.get("turn", 1))
        if transmitted and remaining > 1:
            root_key = str(payload.get("root_key", event.dedupe_key))
            _next, inserted = await store.schedule_once(
                session,
                dedupe_key=f"{root_key}:turn:{turn + 1}",
                kind="npc_conversation",
                due_minute=event.due_minute + 1,
                priority=70,
                actor_id=listener.content_id,
                target_id=actor.content_id,
                room_id=actor.room_id,
                payload={
                    **payload,
                    "turn": turn + 1,
                    "remaining_turns": remaining - 1,
                },
            )
            counters.writes += int(inserted)

        store.resolve_scheduled_event(
            event, world_minute=event.due_minute,
        )
        counters.conversations += 1
        counters.processed_events += 1
        counters.writes += 1
        return True


async def advance(
    session: AsyncSession,
    wall_now: float,
    active_room_ids: Collection[int],
    *,
    config: LivingWorldConfig = DEFAULT_CONFIG,
    content: LivingWorldContent | Any | None = None,
) -> AdvanceResult:
    """Convenience API used by the application-level world ticker."""
    return await LivingWorldService(
        config=config,
        content=content,
    ).advance(session, wall_now, active_room_ids)


def _stable_u64(*parts: object) -> int:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(
        hashlib.blake2b(material, digest_size=8).digest(), "big",
    )


def _stable_unit(*parts: object) -> float:
    return _stable_u64(*parts) / (2**64 - 1)
