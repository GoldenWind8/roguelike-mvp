import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import backend.main as main
import backend.situation_store as situation_store
from backend.db import Base
from backend.entities import Position
from backend.events import EventType, GameEvent
from backend.living_world.player_knowledge import (
    record_object_discovery,
    world_sync,
)
from backend.living_world.service import LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.models import (
    NPCGoal,
    NPCRow,
    PlayerKnowledge,
    PlayerRow,
    Room,
    ScheduledWorldEvent,
    WorldEvent,
    WorldFact,
)
from backend.object_defs import get_object_definition
from backend.room_loader import RoomObject
from backend.seeds import get_or_seed_default_room
from backend.situation_defs import get_situation
from backend.situation_store import (
    record_situation_actor_defeat,
    resolve_situation_choice,
    situation_view,
)


async def _gate_player(session, player_id: str = "player_gate_reader"):
    await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())
    gate = (await session.execute(
        select(Room).where(Room.content_id == "drazna_gate_seven")
    )).scalar_one()
    player = PlayerRow(
        id=player_id,
        username=player_id,
        password_hash="unused",
        room_id=gate.id,
        x=4,
        y=8,
        hp=100,
    )
    session.add(player)
    await session.commit()
    return gate, player


async def _discover(session, player, room, object_type: str, minute: int):
    definition = get_object_definition(object_type)
    assert definition is not None and definition.discovery is not None
    return await record_object_discovery(
        session,
        player_id=player.id,
        room_id=room.id,
        object_id=f"placed:{object_type}",
        discovery=definition.discovery,
        world_minute=minute,
    )


async def test_gate_seven_choices_are_revealed_only_by_personal_evidence(session):
    gate, player = await _gate_player(session)
    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None

    hidden = await situation_view(
        session,
        definition=definition,
        player_id=player.id,
    )
    assert hidden["choices"] == []
    assert hidden["resolved"] is False

    for minute, object_type in enumerate(
        (
            "drazna_sluice_tools",
            "drazna_listening_pipe",
            "drazna_omitted_tablets",
        ),
        start=100,
    ):
        await _discover(session, player, gate, object_type, minute)
    await session.commit()

    learned = await situation_view(
        session,
        definition=definition,
        player_id=player.id,
    )
    assert [choice["id"] for choice in learned["choices"]] == [
        "answer-the-fourteenth"
    ]
    # The client is never sent the hidden alternative or missing clue ids.
    assert "brace-the-counterpressure" not in str(learned)
    assert "counterpressure-scale" not in str(learned)


async def test_gate_situation_cannot_use_an_in_transit_actors_stale_room(
    session,
):
    gate, player = await _gate_player(session)
    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None
    odran = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == definition.actor_id)
    )).scalar_one()
    destination = (await session.execute(
        select(Room).where(
            Room.content_id == "drazna_pressure_gallery"
        )
    )).scalar_one()
    for minute, object_type in enumerate(
        (
            "drazna_sluice_tools",
            "drazna_listening_pipe",
            "drazna_omitted_tablets",
        ),
        start=100,
    ):
        await _discover(session, player, gate, object_type, minute)
    pending = ScheduledWorldEvent(
        dedupe_key="journey:test-odran-hidden-from-situation",
        kind="npc_arrive_room",
        due_minute=200,
        priority=10,
        status="pending",
        actor_id=odran.content_id,
        room_id=gate.id,
        payload={
            "route_room_ids": [gate.id, destination.id],
            "step_index": 1,
            "from_room_id": gate.id,
            "to_room_id": destination.id,
            "final_room_id": destination.id,
        },
    )
    session.add(pending)
    await session.commit()

    with pytest.raises(
        situation_store.SituationError,
        match="can no longer be found",
    ):
        await situation_view(
            session,
            definition=definition,
            player_id=player.id,
        )
    with pytest.raises(
        situation_store.SituationError,
        match="moment for that answer has passed",
    ):
        await resolve_situation_choice(
            session,
            definition=definition,
            choice_id="answer-the-fourteenth",
            player_id=player.id,
            room_id=gate.id,
            world_minute=150,
        )

    pending.status = "cancelled"
    await session.commit()
    visible = await situation_view(
        session,
        definition=definition,
        player_id=player.id,
    )
    assert [choice["id"] for choice in visible["choices"]] == [
        "answer-the-fourteenth"
    ]


async def test_gate_seven_resolution_is_exclusive_and_updates_the_living_actor(
    session,
):
    gate, player = await _gate_player(session)
    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None
    for minute, object_type in enumerate(
        (
            "drazna_sluice_tools",
            "drazna_listening_pipe",
            "drazna_omitted_tablets",
            "drazna_pressure_gauge",
            "drazna_crown_flood_order",
        ),
        start=200,
    ):
        await _discover(session, player, gate, object_type, minute)

    first = await resolve_situation_choice(
        session,
        definition=definition,
        choice_id="answer-the-fourteenth",
        player_id=player.id,
        room_id=gate.id,
        world_minute=240,
    )
    assert first.inserted is True
    assert first.outcome == "pacified"

    # A delayed or forged competing request reads the already-committed truth.
    second = await resolve_situation_choice(
        session,
        definition=definition,
        choice_id="brace-the-counterpressure",
        player_id=player.id,
        room_id=gate.id,
        world_minute=241,
    )
    assert second.inserted is False
    assert second.outcome == "pacified"
    assert second.actor_disposition == "neutral"
    await session.commit()
    await advance_authored_triggers(
        session,
        from_minute=240,
        to_minute=242,
        active_room_ids=(),
    )

    fact = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "drazna.gate_seven_resolution"
        )
    )).scalar_one()
    assert fact.value == {
        "state": "pacified",
        "gate": "vented",
        "names_spoken": 14,
    }
    odran = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "odran-third-bell")
    )).scalar_one()
    assert odran.disposition == "neutral"
    goal = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == "odran-third-bell",
            NPCGoal.goal_key == "answer-fourteen",
        )
    )).scalar_one()
    assert (goal.status, goal.progress) == ("completed", 1.0)
    rada_goal = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == "rada-velic",
            NPCGoal.goal_key == "stabilize-gate-seven",
        )
    )).scalar_one()
    assert rada_goal.status == "completed"
    ward = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "drazna.walking_ward_after_gate"
        )
    )).scalar_one()
    assert ward.value == {"state": "stabilized", "evacuated_houses": 0}
    remembered = (await session.execute(
        select(PlayerKnowledge).where(
            PlayerKnowledge.player_id == player.id,
            PlayerKnowledge.knowledge_key
            == "situation:drazna-gate-seven-reckoning",
        )
    )).scalar_one()
    assert remembered.provenance == "witnessed"
    payload = await world_sync(
        session,
        player_id=player.id,
        current_room_id=gate.id,
    )
    chronicle_entry = next(
        entry
        for entry in payload["chronicle"]
        if entry["id"] == f"knowledge:{remembered.id}"
    )
    assert chronicle_entry["provenance"] == "witnessed"


async def test_competing_gate_choices_share_one_atomic_canonical_outcome(
    tmp_path,
    monkeypatch,
):
    """Two database sessions cannot split fact, Chronicle, or actor truth."""
    database_path = tmp_path / "gate-choice-race.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with maker() as session:
            await get_or_seed_default_room(session)
            await LivingWorldService().advance(session, 0, ())
            gate = (await session.execute(
                select(Room).where(Room.content_id == "drazna_gate_seven")
            )).scalar_one()
            for player_id in ("gate-race-reader", "gate-race-engineer"):
                session.add(PlayerRow(
                    id=player_id,
                    username=player_id,
                    password_hash="unused",
                    room_id=gate.id,
                    x=4,
                    y=8,
                    hp=100,
                ))
            await session.commit()
            for player_id in ("gate-race-reader", "gate-race-engineer"):
                player = await session.get(PlayerRow, player_id)
                for minute, object_type in enumerate(
                    (
                        "drazna_sluice_tools",
                        "drazna_listening_pipe",
                        "drazna_omitted_tablets",
                        "drazna_pressure_gauge",
                        "drazna_crown_flood_order",
                    ),
                    start=900,
                ):
                    await _discover(
                        session,
                        player,
                        gate,
                        object_type,
                        minute,
                    )
            await session.commit()
            gate_id = gate.id

        definition = get_situation("drazna-gate-seven-reckoning")
        assert definition is not None
        original_known_clues = situation_store._known_clues
        both_read_unresolved = asyncio.Barrier(2)

        async def synchronized_known_clues(session, player_id):
            clues = await original_known_clues(session, player_id)
            # Each resolve has already read the outcome as absent. Releasing
            # both here deterministically exercises the unique-key claim.
            await both_read_unresolved.wait()
            return clues

        monkeypatch.setattr(
            situation_store,
            "_known_clues",
            synchronized_known_clues,
        )

        async def resolve(player_id: str, choice_id: str):
            async with maker() as session:
                result = await resolve_situation_choice(
                    session,
                    definition=definition,
                    choice_id=choice_id,
                    player_id=player_id,
                    room_id=gate_id,
                    world_minute=920,
                    witnesses=(
                        "gate-race-reader",
                        "gate-race-engineer",
                    ),
                )
                await session.commit()
                return result

        results = await asyncio.gather(
            resolve("gate-race-reader", "answer-the-fourteenth"),
            resolve("gate-race-engineer", "brace-the-counterpressure"),
        )
        assert {result.outcome for result in results} in (
            {"pacified"},
            {"contained"},
        )
        assert sorted(result.inserted for result in results) == [False, True]

        outcome = results[0].outcome
        winning_choice = next(
            choice
            for choice in definition.choices
            if choice.outcome == outcome
        )
        async with maker() as session:
            facts = (await session.execute(
                select(WorldFact).where(
                    WorldFact.fact_key == definition.fact_key
                )
            )).scalars().all()
            events = (await session.execute(
                select(WorldEvent).where(
                    WorldEvent.dedupe_key == f"situation:{definition.id}"
                )
            )).scalars().all()
            actor = (await session.execute(
                select(NPCRow).where(
                    NPCRow.content_id == definition.actor_id
                )
            )).scalar_one()
            goal = (await session.execute(
                select(NPCGoal).where(
                    NPCGoal.npc_content_id == definition.actor_id,
                    NPCGoal.goal_key == winning_choice.actor_goal_id,
                )
            )).scalar_one()
            memories = (await session.execute(
                select(PlayerKnowledge).where(
                    PlayerKnowledge.knowledge_key
                    == f"situation:{definition.id}"
                )
            )).scalars().all()

        assert len(facts) == len(events) == 1
        assert facts[0].value == winning_choice.fact_value
        assert facts[0].source_event_id == events[0].id
        assert events[0].payload["outcome"] == outcome
        assert events[0].summary == winning_choice.chronicle
        assert events[0].witnesses == [
            "gate-race-engineer",
            "gate-race-reader",
        ]
        assert actor.disposition == winning_choice.actor_disposition
        assert goal.status == winning_choice.actor_goal_status
        assert {memory.player_id for memory in memories} == {
            "gate-race-reader",
            "gate-race-engineer",
        }
        assert {
            memory.payload["outcome"]
            for memory in memories
        } == {outcome}
        assert {
            memory.payload["source_event_id"]
            for memory in memories
        } == {events[0].id}
    finally:
        await engine.dispose()


async def test_gate_seven_counterpressure_is_a_distinct_canonical_outcome(session):
    gate, player = await _gate_player(session, "player_gate_engineer")
    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None
    await _discover(session, player, gate, "drazna_pressure_gauge", 300)
    await _discover(session, player, gate, "drazna_crown_flood_order", 301)

    resolution = await resolve_situation_choice(
        session,
        definition=definition,
        choice_id="brace-the-counterpressure",
        player_id=player.id,
        room_id=gate.id,
        world_minute=310,
    )
    await session.commit()
    assert resolution.outcome == "contained"
    fact = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "drazna.gate_seven_resolution"
        )
    )).scalar_one()
    assert fact.value == {
        "state": "contained",
        "gate": "braced",
        "names_spoken": 0,
    }


async def test_gate_seven_actor_defeat_records_one_witnessed_final_outcome(session):
    gate, player = await _gate_player(session, "player_gate_witness")
    odran = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "odran-third-bell")
    )).scalar_one()
    odran.hp = 0
    odran.is_alive = False

    first = await record_situation_actor_defeat(
        session,
        actor_id="odran-third-bell",
        room_id=gate.id,
        world_minute=410,
        witnesses=(player.id,),
    )
    second = await record_situation_actor_defeat(
        session,
        actor_id="odran-third-bell",
        room_id=gate.id,
        world_minute=411,
        witnesses=(player.id,),
    )
    await session.commit()
    await advance_authored_triggers(
        session,
        from_minute=410,
        to_minute=412,
        active_room_ids=(),
    )
    assert first is not None and first.inserted is True
    assert second is not None and second.inserted is False
    assert first.outcome == second.outcome == "odran-killed"

    fact = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "drazna.gate_seven_resolution"
        )
    )).scalar_one()
    assert fact.value == {
        "state": "odran-killed",
        "gate": "held-by-chain",
        "names_spoken": 0,
    }
    goals = {
        (goal.npc_content_id, goal.goal_key): goal.status
        for goal in (await session.execute(
            select(NPCGoal).where(
                NPCGoal.npc_content_id.in_({
                    "odran-third-bell",
                    "rada-velic",
                })
            )
        )).scalars()
    }
    assert goals[("odran-third-bell", "hold-gate-seven")] == "failed"
    assert goals[("odran-third-bell", "answer-fourteen")] == "failed"
    assert goals[("rada-velic", "stabilize-gate-seven")] == "blocked"
    events = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.dedupe_key
            == "situation:drazna-gate-seven-reckoning"
        )
    )).scalars().all()
    assert len(events) == 1
    assert events[0].visibility == "witnessed"
    assert events[0].witnesses == [player.id]


async def test_authored_object_discovery_is_durable_and_idempotent(session):
    gate, player = await _gate_player(session, "player_clue_reader")
    first, inserted_first = await _discover(
        session,
        player,
        gate,
        "drazna_preproclamation_roll",
        500,
    )
    second, inserted_second = await _discover(
        session,
        player,
        gate,
        "drazna_preproclamation_roll",
        700,
    )
    await session.commit()
    assert inserted_first is True
    assert inserted_second is False
    assert first.id == second.id
    assert first.knowledge_key == "drazna:preproclamation-silt"
    assert first.body.endswith("not where it came from.")


async def test_situation_resolution_does_not_leak_to_a_remote_nonwitness(
    session,
):
    gate, player = await _gate_player(session, "player_gate_private")
    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None
    for minute, object_type in enumerate(
        (
            "drazna_sluice_tools",
            "drazna_listening_pipe",
            "drazna_omitted_tablets",
        ),
        start=800,
    ):
        await _discover(session, player, gate, object_type, minute)

    distant = (await session.execute(
        select(Room).where(Room.id != gate.id).order_by(Room.id)
    )).scalars().first()
    observer = PlayerRow(
        id="player_gate_remote",
        username="player_gate_remote",
        password_hash="unused",
        room_id=distant.id,
        x=distant.spawn_points[0][0],
        y=distant.spawn_points[0][1],
        hp=100,
    )
    session.add(observer)
    resolution = await resolve_situation_choice(
        session,
        definition=definition,
        choice_id="answer-the-fourteenth",
        player_id=player.id,
        room_id=gate.id,
        world_minute=820,
        witnesses=(player.id,),
    )
    await session.commit()

    payload = await world_sync(
        session,
        player_id=observer.id,
        current_room_id=distant.id,
    )
    bodies = {entry["body"] for entry in payload["chronicle"]}
    assert resolution.result not in bodies
    assert not any(
        entry["body"].startswith("Fourteen names were answered")
        for entry in payload["chronicle"]
    )


def test_situation_requires_manhattan_adjacency_to_the_authored_object():
    obj = RoomObject(
        id="drazna_gate_drum",
        type="drazna_gate_chain_drum",
        position=(5, 5),
        label="Gate Seven Chain Drum",
        description="A chained drum.",
        footprint=((0, 0), (1, 0)),
        interaction="situation",
    )
    room = SimpleNamespace(
        get_object=lambda object_id: (
            obj if object_id == obj.id else None
        )
    )
    player = SimpleNamespace(
        is_alive=True,
        position=Position(4, 5),
    )

    _object, definition, error = main._adjacent_situation(
        room,
        player,
        obj.id,
    )
    assert error is None
    assert definition is not None

    player.position = Position(4, 4)
    _object, definition, error = main._adjacent_situation(
        room,
        player,
        obj.id,
    )
    assert definition is None
    assert error == "You are too far away to make out its answer."


async def test_active_odran_death_and_situation_outcome_are_jointly_idempotent(
    monkeypatch,
):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as session:
        await get_or_seed_default_room(session)
        gate = (await session.execute(
            select(Room).where(Room.content_id == "drazna_gate_seven")
        )).scalar_one()

    monkeypatch.setattr(main, "SessionMaker", maker)
    main.active_rooms.clear()
    main.player_room.clear()
    try:
        runtime = await main.get_or_load_room(gate.id)
        player, _events = runtime.engine.join("Gate Witness")
        odran = next(
            npc
            for npc in runtime.engine.room.npcs.values()
            if npc.persona.get("id") == "odran-third-bell"
        )
        odran.hp = 0
        odran.is_alive = False
        death = GameEvent(
            EventType.NPC_DIED,
            {"target_id": odran.id, "killer_id": player.id},
            runtime.engine.room.round,
        )
        async with main.state_lock:
            await main.handle_round_events(runtime, [death])
            await main.handle_round_events(runtime, [death])
        # Let the deliberately fire-and-forget knowledge notifications observe
        # that this ephemeral test player has no registered room.
        await asyncio.sleep(0)

        async with maker() as session:
            saved = await session.get(NPCRow, odran.db_id)
            assert saved.is_alive is False
            assert saved.hp == 0
            death_events = (await session.execute(
                select(WorldEvent).where(
                    WorldEvent.dedupe_key
                    == "npc-death:odran-third-bell"
                )
            )).scalars().all()
            outcome_events = (await session.execute(
                select(WorldEvent).where(
                    WorldEvent.dedupe_key
                    == "situation:drazna-gate-seven-reckoning"
                )
            )).scalars().all()
            assert len(death_events) == len(outcome_events) == 1
            outcome = (await session.execute(
                select(WorldFact).where(
                    WorldFact.fact_key
                    == "drazna.gate_seven_resolution"
                )
            )).scalar_one()
            assert outcome.value["state"] == "odran-killed"
            fate = (await session.execute(
                select(WorldFact).where(
                    WorldFact.fact_key == "npc-fate:odran-third-bell"
                )
            )).scalar_one()
            assert fate.value["is_alive"] is False
    finally:
        main.active_rooms.clear()
        main.player_room.clear()
        await engine.dispose()
