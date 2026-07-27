import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.living_world.store as living_store
from backend.db import Base
from backend.living_world.player_knowledge import (
    record_object_discovery,
    world_sync,
)
from backend.living_world.service import LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.living_world_content import load_living_world_content
from backend.models import (
    NPCMemory,
    NPCRow,
    PlayerRow,
    Room,
    TriggerFiring,
    WorldEvent,
    WorldFact,
    WorldState,
)
from backend.object_defs import get_object_definition
from backend.seeds import get_or_seed_default_room
from backend.situation_defs import get_situation
from backend.situation_store import (
    record_situation_actor_defeat,
    resolve_situation_choice,
    situation_view,
)


_DAY = 1440
_TRIGGER_WATERMARK_KEY = "authored_triggers_through_minute"
_TRIGGER_DEFERRED_KEY = "authored_trigger_deferred_from"


def _database(path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path.as_posix()}",
        connect_args={"timeout": 10},
    )
    return engine, async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )


async def _npc(session, content_id: str) -> NPCRow:
    return (await session.execute(
        select(NPCRow).where(NPCRow.content_id == content_id)
    )).scalar_one()


async def _rooms(session) -> dict[str, Room]:
    return {
        room.content_id: room
        for room in (await session.execute(select(Room))).scalars()
        if room.content_id
    }


def _recurring_trigger():
    return {
        "id": "restart-recurring-window",
        "kind": "story",
        "participants": ["fen-alder"],
        "window": {
            "opens_day": 0,
            "closes_day": None,
            "cooldown_minutes": 300,
            "max_firings": 100,
        },
        "conditions": [{
            "kind": "npc_alive",
            "npc_id": "fen-alder",
            "value": True,
        }],
        "effects": [{
            "kind": "remember",
            "npc_id": "fen-alder",
            "summary": "Fen checked the same restart-safe tally.",
            "importance": 4,
            "tags": ["person", "testimony"],
        }],
        "conversation": None,
        "missed_consequences": [],
        "aftermath_clues": [],
    }


async def _advance_isolated(
    database_path,
    *,
    content,
    from_minute: int,
    to_minute: int,
    active_room_ids=(),
):
    engine, maker = _database(database_path)
    try:
        async with maker() as session:
            return await advance_authored_triggers(
                session,
                from_minute=from_minute,
                to_minute=to_minute,
                active_room_ids=active_room_ids,
                content=content,
            )
    finally:
        await engine.dispose()


async def test_trigger_watermark_survives_restart_without_historical_replay(
    tmp_path,
):
    """The durable checkpoint owns the range; only deferred work may retry."""
    database_path = tmp_path / "trigger-watermark-restart.db"
    engine, maker = _database(database_path)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with maker() as session:
            await get_or_seed_default_room(session)
            await LivingWorldService().advance(session, 0, ())
            fen = await _npc(session, "fen-alder")
            fen_room_id = fen.room_id

            # Exercise a genuinely uninitialized consumer. Minute zero must
            # run once rather than being mistaken for an exact replay.
            state = await session.get(WorldState, 1)
            variables = dict(state.variables or {})
            variables.pop(_TRIGGER_WATERMARK_KEY, None)
            state.variables = variables
            await session.commit()
    finally:
        await engine.dispose()

    trigger = _recurring_trigger()
    content = replace(
        load_living_world_content(),
        triggers={trigger["id"]: trigger},
    )

    first = await _advance_isolated(
        database_path,
        content=content,
        from_minute=0,
        to_minute=0,
    )
    assert first.fired == 1
    exact_zero = await _advance_isolated(
        database_path,
        content=content,
        from_minute=0,
        to_minute=0,
    )
    assert exact_zero.fired == 0

    forward = await _advance_isolated(
        database_path,
        content=content,
        from_minute=0,
        to_minute=600,
    )
    assert forward.fired == 1
    historical = await _advance_isolated(
        database_path,
        content=content,
        from_minute=0,
        to_minute=599,
    )
    exact = await _advance_isolated(
        database_path,
        content=content,
        from_minute=0,
        to_minute=600,
    )
    assert historical.fired == exact.fired == 0

    # A producer can commit farther ahead before the trigger transaction.
    # The caller knows only the new point; the older durable watermark must
    # still restore the unconsumed 600..1200 interval after restart.
    engine, maker = _database(database_path)
    try:
        async with maker() as session:
            state = await session.get(WorldState, 1)
            state.world_minute = 1200
            await session.commit()
    finally:
        await engine.dispose()
    crash_catchup = await _advance_isolated(
        database_path,
        content=content,
        from_minute=1200,
        to_minute=1200,
    )
    assert crash_catchup.fired == 1

    protected = await _advance_isolated(
        database_path,
        content=content,
        from_minute=1200,
        to_minute=1800,
        active_room_ids=(fen_room_id,),
    )
    assert protected.fired == 0

    # At the same committed watermark, only the explicitly deferred trigger
    # may resume when its room stops being active.
    resumed = await _advance_isolated(
        database_path,
        content=content,
        from_minute=1800,
        to_minute=1800,
    )
    replayed_resume = await _advance_isolated(
        database_path,
        content=content,
        from_minute=1800,
        to_minute=1800,
    )
    assert resumed.fired == 1
    assert replayed_resume.fired == 0

    engine, maker = _database(database_path)
    try:
        async with maker() as session:
            firings = (await session.execute(
                select(TriggerFiring).where(
                    TriggerFiring.trigger_id == trigger["id"],
                ).order_by(TriggerFiring.ordinal)
            )).scalars().all()
            memories = (await session.execute(
                select(NPCMemory).where(
                    NPCMemory.summary
                    == "Fen checked the same restart-safe tally.",
                )
            )).scalars().all()
            state = await session.get(WorldState, 1)
            assert [
                (row.ordinal, row.fired_at_minute)
                for row in firings
            ] == [
                (1, 0),
                (2, 600),
                (3, 1200),
                (4, 1800),
            ]
            assert len(memories) == 4
            assert state.variables[_TRIGGER_WATERMARK_KEY] == 1800
            assert _TRIGGER_DEFERRED_KEY not in state.variables
    finally:
        await engine.dispose()


async def _discover(
    session,
    *,
    player: PlayerRow,
    room: Room,
    object_type: str,
    world_minute: int,
) -> None:
    definition = get_object_definition(object_type)
    assert definition is not None and definition.discovery is not None
    await record_object_discovery(
        session,
        player_id=player.id,
        room_id=room.id,
        object_id=f"restart:{player.id}:{object_type}",
        discovery=definition.discovery,
        world_minute=world_minute,
    )


async def test_flooded_gate_is_terminal_across_restart_and_late_actor_death(
    tmp_path,
):
    database_path = tmp_path / "gate-terminal-restart.db"
    engine, maker = _database(database_path)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with maker() as session:
            await get_or_seed_default_room(session)
            await LivingWorldService().advance(session, 0, ())
            rooms = await _rooms(session)
            gate = rooms["drazna_gate_seven"]
            remote = rooms["oakrun_crossroads"]
            session.add_all((
                PlayerRow(
                    id="late-gate-engineer",
                    username="late-gate-engineer",
                    password_hash="unused",
                    room_id=gate.id,
                    x=gate.spawn_points[0][0],
                    y=gate.spawn_points[0][1],
                    hp=100,
                ),
                PlayerRow(
                    id="remote-gate-reader",
                    username="remote-gate-reader",
                    password_hash="unused",
                    room_id=remote.id,
                    x=remote.spawn_points[0][0],
                    y=remote.spawn_points[0][1],
                    hp=100,
                ),
            ))
            pava = await _npc(session, "pava-mirek")
            pava.hp = 0
            pava.is_alive = False
            await session.commit()

            await advance_authored_triggers(
                session,
                from_minute=0,
                to_minute=14 * _DAY,
                active_room_ids=(),
            )
            state = await session.get(WorldState, 1)
            state.world_minute = 14 * _DAY
            await session.commit()
    finally:
        await engine.dispose()
    await _assert_flooded_restart_terminal(database_path)


@pytest.mark.parametrize(
    ("competitor", "expected_competitor_outcome"),
    (
        ("peaceful-choice", "contained"),
        ("actor-defeat", "odran-killed"),
    ),
)
async def test_gate_flood_atomically_races_situation_terminal_outcomes(
    tmp_path,
    monkeypatch,
    competitor,
    expected_competitor_outcome,
):
    """A simultaneous terminal claim beats flood without partial effects."""
    database_path = tmp_path / f"gate-flood-{competitor}-race.db"
    engine, maker = _database(database_path)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None
    authored = load_living_world_content()
    flood_trigger = authored.triggers["gate-seven-unanswered-flood"]
    race_content = replace(
        authored,
        triggers={flood_trigger["id"]: flood_trigger},
    )
    aftermath_ids = {
        "gate-seven-pacified-aftermath",
        "gate-seven-contained-aftermath",
        "gate-seven-killed-aftermath",
        "gate-seven-flood-aftermath",
    }
    aftermath_content = replace(
        authored,
        triggers={
            trigger_id: authored.triggers[trigger_id]
            for trigger_id in aftermath_ids
        },
    )

    try:
        async with maker() as session:
            await get_or_seed_default_room(session)
            await LivingWorldService().advance(session, 0, ())
            rooms = await _rooms(session)
            gate = rooms["drazna_gate_seven"]
            player = PlayerRow(
                id=f"gate-{competitor}-witness",
                username=f"gate-{competitor}-witness",
                password_hash="unused",
                room_id=gate.id,
                x=gate.spawn_points[0][0],
                y=gate.spawn_points[0][1],
                hp=100,
            )
            session.add(player)
            await session.commit()
            if competitor == "peaceful-choice":
                for offset, object_type in enumerate((
                    "drazna_pressure_gauge",
                    "drazna_crown_flood_order",
                )):
                    await _discover(
                        session,
                        player=player,
                        room=gate,
                        object_type=object_type,
                        world_minute=100 + offset,
                    )
                await session.commit()
            gate_id = gate.id
            player_id = player.id
            initial_rada_hp = (await _npc(session, "rada-velic")).hp

        original_claim = living_store.claim_fact_once
        both_ready_to_claim = asyncio.Barrier(2)
        terminal_claimed = asyncio.Event()

        async def synchronized_claim(session, **kwargs):
            if kwargs["fact_key"] == definition.fact_key:
                await both_ready_to_claim.wait()
                if kwargs["value"]["state"] == "flooded":
                    await terminal_claimed.wait()
                    return await original_claim(session, **kwargs)
                result = await original_claim(session, **kwargs)
                terminal_claimed.set()
                return result
            return await original_claim(session, **kwargs)

        monkeypatch.setattr(
            living_store,
            "claim_fact_once",
            synchronized_claim,
        )

        async def flood():
            async with maker() as session:
                return await advance_authored_triggers(
                    session,
                    from_minute=0,
                    to_minute=13 * _DAY,
                    active_room_ids=(),
                    content=race_content,
                )

        async def resolve_competitor():
            async with maker() as session:
                if competitor == "peaceful-choice":
                    result = await resolve_situation_choice(
                        session,
                        definition=definition,
                        choice_id="brace-the-counterpressure",
                        player_id=player_id,
                        room_id=gate_id,
                        world_minute=13 * _DAY,
                        witnesses=(player_id,),
                    )
                else:
                    result = await record_situation_actor_defeat(
                        session,
                        actor_id=definition.actor_id,
                        room_id=gate_id,
                        world_minute=13 * _DAY,
                        witnesses=(player_id,),
                    )
                    assert result is not None
                await session.commit()
                return result

        flood_result, competitor_result = await asyncio.gather(
            flood(),
            resolve_competitor(),
        )

        async with maker() as session:
            resolution_rows = (await session.execute(
                select(WorldFact).where(
                    WorldFact.fact_key == definition.fact_key
                )
            )).scalars().all()
            assert len(resolution_rows) == 1
            resolution = resolution_rows[0]
            outcome = resolution.value["state"]
            assert outcome == expected_competitor_outcome
            assert competitor_result.outcome == outcome
            assert competitor_result.inserted is True
            assert flood_result.fired == 0
            assert flood_result.effects_applied == 0

            flood_firings = (await session.execute(
                select(TriggerFiring).where(
                    TriggerFiring.trigger_id
                    == "gate-seven-unanswered-flood"
                )
            )).scalars().all()
            flood_events = (await session.execute(
                select(WorldEvent).where(
                    WorldEvent.dedupe_key
                    == "trigger-event:gate-seven-unanswered-flood:1"
                )
            )).scalars().all()
            situation_events = (await session.execute(
                select(WorldEvent).where(
                    WorldEvent.dedupe_key
                    == "situation:drazna-gate-seven-reckoning"
                )
            )).scalars().all()
            climax_rows = (await session.execute(
                select(WorldFact).where(
                    WorldFact.fact_key == "drazna.gate_seven_climax"
                )
            )).scalars().all()
            flood_evidence = (await session.execute(
                select(WorldEvent).where(
                    WorldEvent.dedupe_key.like(
                        "evidence:gate-seven-unanswered-flood:%"
                    )
                )
            )).scalars().all()
            rada = await _npc(session, "rada-velic")

            flood_won = outcome == "flooded"
            assert len(flood_firings) == int(flood_won)
            assert len(flood_events) == int(flood_won)
            assert len(situation_events) == int(not flood_won)
            assert len(climax_rows) == int(flood_won)
            assert len(flood_evidence) == (2 if flood_won else 0)
            assert rada.hp == (
                initial_rada_hp - 20 if flood_won else initial_rada_hp
            )
            winning_event = (
                flood_events[0] if flood_won else situation_events[0]
            )
            assert resolution.source_event_id == winning_event.id

            aftermath_result = await advance_authored_triggers(
                session,
                from_minute=13 * _DAY,
                to_minute=13 * _DAY + 1,
                active_room_ids=(),
                content=aftermath_content,
            )
            assert aftermath_result.fired == 1

        matching_aftermath = {
            "flooded": "gate-seven-flood-aftermath",
            "contained": "gate-seven-contained-aftermath",
            "odran-killed": "gate-seven-killed-aftermath",
        }[outcome]
        async with maker() as session:
            firings = {
                firing.trigger_id
                for firing in (await session.execute(
                    select(TriggerFiring).where(
                        TriggerFiring.trigger_id.in_(aftermath_ids)
                    )
                )).scalars()
            }
            assert firings == {matching_aftermath}
            ward = (await session.execute(
                select(WorldFact).where(
                    WorldFact.fact_key
                    == "drazna.walking_ward_after_gate"
                )
            )).scalar_one_or_none()
            if outcome == "flooded":
                assert ward is not None
                assert ward.value == {
                    "state": "partly-collapsed",
                    "evacuated_houses": 3,
                }
            elif outcome == "contained":
                assert ward is not None
                assert ward.value == {
                    "state": "stabilized",
                    "evacuated_houses": 0,
                }
            else:
                assert ward is None
    finally:
        await engine.dispose()


async def _assert_flooded_restart_terminal(database_path):
    # A new process opens the already-flooded world. Even complete evidence
    # cannot revive the earlier peaceful window.
    engine, maker = _database(database_path)
    try:
        async with maker() as session:
            rooms = await _rooms(session)
            gate = rooms["drazna_gate_seven"]
            remote = rooms["oakrun_crossroads"]
            engineer = await session.get(PlayerRow, "late-gate-engineer")
            for offset, object_type in enumerate((
                "drazna_pressure_gauge",
                "drazna_crown_flood_order",
            )):
                await _discover(
                    session,
                    player=engineer,
                    room=gate,
                    object_type=object_type,
                    world_minute=14 * _DAY + offset,
                )
            await session.commit()

            definition = get_situation("drazna-gate-seven-reckoning")
            assert definition is not None
            view = await situation_view(
                session,
                definition=definition,
                player_id=engineer.id,
            )
            assert view["resolved"] is True
            assert view["outcome"] == "flooded"
            assert view["choices"] == []

            forged_late_choice = await resolve_situation_choice(
                session,
                definition=definition,
                choice_id="brace-the-counterpressure",
                player_id=engineer.id,
                room_id=gate.id,
                world_minute=14 * _DAY + 3,
                witnesses=(engineer.id,),
            )
            assert forged_late_choice.outcome == "flooded"
            assert forged_late_choice.inserted is False

            odran = await _npc(session, "odran-third-bell")
            odran.hp = 0
            odran.is_alive = False
            late_defeat = await record_situation_actor_defeat(
                session,
                actor_id=odran.content_id,
                room_id=gate.id,
                world_minute=14 * _DAY + 4,
                witnesses=(engineer.id,),
            )
            assert late_defeat is not None
            assert late_defeat.outcome == "flooded"
            assert late_defeat.inserted is False
            await session.commit()

            remote_payload = await world_sync(
                session,
                player_id="remote-gate-reader",
                current_room_id=remote.id,
            )
            remote_bodies = {
                entry["body"] for entry in remote_payload["chronicle"]
            }
            assert not any(
                "Gate Seven" in body
                or "pressure needle" in body
                or "chain shudders" in body
                for body in remote_bodies
            )
    finally:
        await engine.dispose()

    # A second restart and a long historical replay cannot add a competing
    # outcome, repair the collapsed ward, or leak the private terminal state.
    engine, maker = _database(database_path)
    try:
        async with maker() as session:
            advanced = await advance_authored_triggers(
                session,
                from_minute=14 * _DAY,
                to_minute=60 * _DAY,
                active_room_ids=(),
            )
            exact_replay = await advance_authored_triggers(
                session,
                from_minute=14 * _DAY,
                to_minute=60 * _DAY,
                active_room_ids=(),
            )
            historical_replay = await advance_authored_triggers(
                session,
                from_minute=0,
                to_minute=30 * _DAY,
                active_room_ids=(),
            )
            assert advanced.fired >= 0
            assert exact_replay.fired == 0
            assert historical_replay.fired == 0

            facts = {
                fact.fact_key: fact.value
                for fact in (await session.execute(
                    select(WorldFact).where(
                        WorldFact.fact_key.like("drazna.%")
                    )
                )).scalars()
            }
            assert facts["drazna.gate_seven_resolution"] == {
                "state": "flooded",
                "gate": "jammed",
                "names_spoken": 9,
            }
            assert facts["drazna.gate_seven_climax"] == {
                "state": "flooded",
                "gate": "jammed",
                "cadence_known": True,
            }
            assert facts["drazna.walking_ward_after_gate"] == {
                "state": "partly-collapsed",
                "evacuated_houses": 3,
            }

            firings = {
                firing.trigger_id
                for firing in (await session.execute(
                    select(TriggerFiring)
                )).scalars()
            }
            aftermaths = {
                "gate-seven-pacified-aftermath",
                "gate-seven-contained-aftermath",
                "gate-seven-killed-aftermath",
                "gate-seven-flood-aftermath",
            } & firings
            assert aftermaths == {"gate-seven-flood-aftermath"}
            assert "gate-seven-unanswered-flood" in firings
            situation_events = (await session.execute(
                select(WorldEvent).where(
                    WorldEvent.dedupe_key
                    == "situation:drazna-gate-seven-reckoning"
                )
            )).scalars().all()
            assert situation_events == []
            assert (await _npc(session, "odran-third-bell")).is_alive is False

            rooms = await _rooms(session)
            gate = rooms["drazna_gate_seven"]
            observer = await session.get(PlayerRow, "remote-gate-reader")
            observer.room_id = gate.id
            observer.x, observer.y = gate.spawn_points[0]
            await session.commit()
            local_payload = await world_sync(
                session,
                player_id=observer.id,
                current_room_id=gate.id,
            )
            local_bodies = {
                entry["body"] for entry in local_payload["chronicle"]
            }
            assert (
                "Gate Seven's chain shudders under a pressure surge while "
                "warning bells answer from Walking Ward."
            ) in local_bodies
            assert not any(
                body.startswith((
                    "Fourteen names were answered",
                    "Gate Seven was rebraced",
                    "Odran Third-Bell was slain",
                ))
                for body in local_bodies
            )
    finally:
        await engine.dispose()
