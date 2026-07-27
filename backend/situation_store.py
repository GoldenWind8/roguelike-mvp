"""Persistent resolution for evidence-gated authored situations.

The database owns exclusivity.  The client receives only choices whose clue
requirements the requesting player has already satisfied, and the first
committed outcome becomes durable world truth.  Later living-world triggers
may react to that fact, but no per-player quest state is created.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.living_world import store
from backend.models import (
    NPCGoal,
    NPCRow,
    PlayerKnowledge,
    Room,
    WorldFact,
)
from backend.situation_defs import (
    SituationChoice,
    SituationDefinition,
    SituationTerminalOutcome,
    get_situation_for_actor,
)


class SituationError(ValueError):
    """A safe refusal suitable for returning to one player."""


@dataclass(frozen=True)
class SituationResolution:
    situation_id: str
    outcome: str
    result: str
    actor_id: str
    actor_disposition: str | None
    inserted: bool


async def situation_view(
    session: AsyncSession,
    *,
    definition: SituationDefinition,
    player_id: str,
) -> dict:
    fact = await _outcome_fact(session, definition)
    if fact is not None:
        outcome = _fact_outcome(fact)
        return {
            "id": definition.id,
            "object_id": definition.object_id,
            "title": definition.title,
            "kicker": definition.kicker,
            "description": definition.description,
            "resolved": True,
            "outcome": outcome,
            "result": _result_for(definition, outcome),
            "choices": [],
        }

    terminal = await _terminal_outcome(session, definition)
    if terminal is not None:
        return {
            "id": definition.id,
            "object_id": definition.object_id,
            "title": definition.title,
            "kicker": definition.kicker,
            "description": definition.description,
            "resolved": True,
            "outcome": terminal.outcome,
            "result": terminal.result,
            "choices": [],
        }

    actor = await _actor(session, definition)
    if actor is None:
        raise SituationError("The person bound to this place can no longer be found.")
    if not actor.is_alive:
        # A crash between durable NPC death and the situation-specific fact
        # must not expose alternate peaceful choices on the next inspection.
        resolution = await record_situation_actor_defeat(
            session,
            actor_id=definition.actor_id,
            room_id=actor.room_id,
            world_minute=(await _world_state(session)).world_minute,
            witnesses=(),
        )
        outcome = resolution.outcome
        return {
            "id": definition.id,
            "object_id": definition.object_id,
            "title": definition.title,
            "kicker": definition.kicker,
            "description": definition.description,
            "resolved": True,
            "outcome": outcome,
            "result": resolution.result,
            "choices": [],
        }
    if await store.has_pending_actor_action(
        session,
        actor_id=definition.actor_id,
        kinds=("npc_arrive_room",),
    ):
        raise SituationError("The person bound to this place can no longer be found.")

    known_clues = await _known_clues(session, player_id)
    return {
        "id": definition.id,
        "object_id": definition.object_id,
        "title": definition.title,
        "kicker": definition.kicker,
        "description": definition.description,
        "resolved": False,
        "outcome": None,
        "result": None,
        # Hidden choices stay absent.  Their missing clue identifiers are
        # intentionally never sent as pseudo-objectives.
        "choices": [
            _choice_view(choice)
            for choice in definition.choices
            if set(choice.requires_all_clues) <= known_clues
        ],
    }


async def resolve_situation_choice(
    session: AsyncSession,
    *,
    definition: SituationDefinition,
    choice_id: object,
    player_id: str,
    room_id: int,
    world_minute: int,
    witnesses: Iterable[str] = (),
) -> SituationResolution:
    if not isinstance(choice_id, str):
        raise SituationError("Choose a response that is actually present.")
    choice = next(
        (candidate for candidate in definition.choices if candidate.id == choice_id),
        None,
    )
    if choice is None:
        raise SituationError("That response is no longer possible.")

    existing = await _outcome_fact(session, definition, lock=True)
    if existing is not None:
        outcome = _fact_outcome(existing)
        return SituationResolution(
            situation_id=definition.id,
            outcome=outcome,
            result=_result_for(definition, outcome),
            actor_id=definition.actor_id,
            actor_disposition=situation_actor_disposition(
                definition,
                outcome,
            ),
            inserted=False,
        )

    terminal = await _terminal_outcome(session, definition, lock=True)
    if terminal is not None:
        return SituationResolution(
            situation_id=definition.id,
            outcome=terminal.outcome,
            result=terminal.result,
            actor_id=definition.actor_id,
            actor_disposition=None,
            inserted=False,
        )

    known_clues = await _known_clues(session, player_id)
    if not set(choice.requires_all_clues) <= known_clues:
        # Do not tell a forged client which evidence it lacks.
        raise SituationError("The mechanism does not answer that attempt.")

    actor = await _actor(session, definition, lock=True)
    if (
        actor is None
        or not actor.is_alive
        or actor.room_id != room_id
        or await store.has_pending_actor_action(
            session,
            actor_id=definition.actor_id,
            kinds=("npc_arrive_room",),
        )
    ):
        raise SituationError("The moment for that answer has passed.")

    goal = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == definition.actor_id,
            NPCGoal.goal_key == choice.actor_goal_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if goal is None:
        raise SituationError("The old cadence no longer has anything to answer.")

    witness_ids = tuple(dict.fromkeys((
        player_id,
        *(
            witness
            for witness in witnesses
            if isinstance(witness, str)
        ),
    )))
    fact, inserted = await _claim_outcome_fact(
        session,
        definition=definition,
        value=choice.fact_value,
        world_minute=world_minute,
    )
    if not inserted:
        outcome = _fact_outcome(fact)
        return SituationResolution(
            situation_id=definition.id,
            outcome=outcome,
            result=_result_for(definition, outcome),
            actor_id=definition.actor_id,
            actor_disposition=situation_actor_disposition(
                definition,
                outcome,
            ),
            inserted=False,
        )

    actor.disposition = choice.actor_disposition
    goal.status = choice.actor_goal_status
    goal.failure_reason = None
    goal.progress = 1.0 if choice.actor_goal_status == "completed" else goal.progress
    goal.last_deliberated_minute = world_minute

    event, _ = await store.chronicle_once(
        session,
        dedupe_key=f"situation:{definition.id}",
        kind="situation_resolved",
        world_minute=world_minute,
        summary=choice.chronicle,
        actor_id=player_id,
        target_id=definition.actor_id,
        room_id=room_id,
        visibility="witnessed",
        witnesses=witness_ids,
        payload={
            "situation_id": definition.id,
            "outcome": choice.outcome,
        },
    )
    fact.source_event_id = event.id
    for witness_id in witness_ids:
        await _remember_resolution(
            session,
            definition=definition,
            player_id=witness_id,
            room_id=room_id,
            world_minute=world_minute,
            outcome=choice.outcome,
            result=choice.result,
            source_event_id=event.id,
        )
    if inserted:
        world = await _world_state(session)
        world.revision += 1
    await session.flush()
    return SituationResolution(
        situation_id=definition.id,
        outcome=choice.outcome,
        result=choice.result,
        actor_id=definition.actor_id,
        actor_disposition=choice.actor_disposition,
        inserted=inserted,
    )


async def record_situation_actor_defeat(
    session: AsyncSession,
    *,
    actor_id: str,
    room_id: int,
    world_minute: int,
    witnesses: tuple[str, ...] | list[str],
) -> SituationResolution | None:
    definition = get_situation_for_actor(actor_id)
    if definition is None:
        return None
    existing = await _outcome_fact(session, definition, lock=True)
    if existing is not None:
        outcome = _fact_outcome(existing)
        return SituationResolution(
            situation_id=definition.id,
            outcome=outcome,
            result=_result_for(definition, outcome),
            actor_id=actor_id,
            actor_disposition=None,
            inserted=False,
        )

    terminal = await _terminal_outcome(session, definition, lock=True)
    if terminal is not None:
        return SituationResolution(
            situation_id=definition.id,
            outcome=terminal.outcome,
            result=terminal.result,
            actor_id=actor_id,
            actor_disposition=None,
            inserted=False,
        )

    defeat = definition.defeat_outcome
    fact, inserted = await _claim_outcome_fact(
        session,
        definition=definition,
        value=defeat.fact_value,
        world_minute=world_minute,
    )
    if not inserted:
        outcome = _fact_outcome(fact)
        return SituationResolution(
            situation_id=definition.id,
            outcome=outcome,
            result=_result_for(definition, outcome),
            actor_id=actor_id,
            actor_disposition=situation_actor_disposition(
                definition,
                outcome,
            ),
            inserted=False,
        )

    witness_ids = tuple(dict.fromkeys(
        witness for witness in witnesses if isinstance(witness, str)
    ))
    event, _ = await store.chronicle_once(
        session,
        dedupe_key=f"situation:{definition.id}",
        kind="situation_resolved",
        world_minute=world_minute,
        summary=defeat.chronicle,
        target_id=actor_id,
        room_id=room_id,
        visibility="witnessed" if witness_ids else "public_aftermath",
        witnesses=witness_ids,
        payload={
            "situation_id": definition.id,
            "outcome": defeat.value,
        },
    )
    fact.source_event_id = event.id
    if inserted:
        world = await _world_state(session)
        world.revision += 1
    await session.flush()
    return SituationResolution(
        situation_id=definition.id,
        outcome=defeat.value,
        result=defeat.result,
        actor_id=actor_id,
        actor_disposition=None,
        inserted=inserted,
    )


async def _outcome_fact(
    session: AsyncSession,
    definition: SituationDefinition,
    *,
    lock: bool = False,
) -> WorldFact | None:
    statement = select(WorldFact).where(
        WorldFact.fact_key == definition.fact_key
    )
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def _terminal_outcome(
    session: AsyncSession,
    definition: SituationDefinition,
    *,
    lock: bool = False,
) -> SituationTerminalOutcome | None:
    """Return the first exact authored terminal fact, if one is durable."""
    if not definition.terminal_outcomes:
        return None
    statement = select(WorldFact).where(
        WorldFact.fact_key.in_(
            terminal.fact_key
            for terminal in definition.terminal_outcomes
        )
    )
    if lock:
        statement = statement.with_for_update()
    rows = {
        row.fact_key: row
        for row in (await session.execute(statement)).scalars()
    }
    return next(
        (
            terminal
            for terminal in definition.terminal_outcomes
            if (
                (fact := rows.get(terminal.fact_key)) is not None
                and (fact.value or {}) == terminal.value
            )
        ),
        None,
    )


async def _claim_outcome_fact(
    session: AsyncSession,
    *,
    definition: SituationDefinition,
    value: dict[str, object],
    world_minute: int,
) -> tuple[WorldFact, bool]:
    """Claim the situation's shared canonical key without replacing a winner."""
    return await store.claim_fact_once(
        session,
        fact_key=definition.fact_key,
        subject_id=definition.id,
        predicate="outcome",
        value=value,
        confidence=1.0,
        visibility="hidden",
        world_minute=world_minute,
    )


async def _world_state(session: AsyncSession):
    world, _created = await store.get_or_create_world_state(
        session,
        wall_now=0.0,
        world_seed=1,
    )
    return world


async def _actor(
    session: AsyncSession,
    definition: SituationDefinition,
    *,
    lock: bool = False,
) -> NPCRow | None:
    statement = select(NPCRow).where(
        NPCRow.content_id == definition.actor_id
    )
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def _known_clues(session: AsyncSession, player_id: str) -> set[str]:
    rows = (await session.execute(
        select(PlayerKnowledge.knowledge_key).where(
            PlayerKnowledge.player_id == player_id,
            PlayerKnowledge.kind == "clue",
        )
    )).scalars().all()
    return set(rows)


async def _remember_resolution(
    session: AsyncSession,
    *,
    definition: SituationDefinition,
    player_id: str,
    room_id: int,
    world_minute: int,
    outcome: str,
    result: str,
    source_event_id: int,
) -> None:
    key = f"situation:{definition.id}"
    existing = (await session.execute(
        select(PlayerKnowledge).where(
            PlayerKnowledge.player_id == player_id,
            PlayerKnowledge.kind == "clue",
            PlayerKnowledge.knowledge_key == key,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return
    room = await session.get(Room, room_id)
    session.add(PlayerKnowledge(
        player_id=player_id,
        kind="clue",
        knowledge_key=key,
        title=definition.title,
        body=result,
        provenance="witnessed",
        learned_at_minute=world_minute,
        source=definition.kicker,
        place=room.name if room else None,
        payload={
            "situation_id": definition.id,
            "outcome": outcome,
            "source_event_id": source_event_id,
        },
    ))


def _choice_view(choice: SituationChoice) -> dict:
    return {
        "id": choice.id,
        "label": choice.label,
        "description": choice.description,
    }


def _fact_outcome(fact: WorldFact) -> str:
    value = fact.value or {}
    outcome = value.get("state", value.get("outcome"))
    return outcome if isinstance(outcome, str) else "unknown"


def _result_for(definition: SituationDefinition, outcome: str) -> str:
    if outcome == definition.defeat_outcome.value:
        return definition.defeat_outcome.result
    terminal = next(
        (
            candidate
            for candidate in definition.terminal_outcomes
            if candidate.outcome == outcome
        ),
        None,
    )
    if terminal is not None:
        return terminal.result
    choice = next(
        (candidate for candidate in definition.choices if candidate.outcome == outcome),
        None,
    )
    return choice.result if choice else "The mechanism has already found its answer."


def situation_actor_disposition(
    definition: SituationDefinition,
    outcome: str | None,
) -> str | None:
    """Return the actor state implied by an already-durable peaceful outcome.

    A request can be retried after the fact commit but before the live room
    applies the result. Replaying the canonical disposition lets the runtime
    repair that narrow stale-state window without reopening the choice.
    """
    choice = next(
        (
            candidate
            for candidate in definition.choices
            if candidate.outcome == outcome
        ),
        None,
    )
    return choice.actor_disposition if choice else None
