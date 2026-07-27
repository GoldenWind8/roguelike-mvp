"""Crash, catch-up, and active-room guarantees for authored world events."""

from dataclasses import replace

import pytest
from sqlalchemy import select

import backend.living_world.trigger_runtime as trigger_runtime
from backend.living_world.service import LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.living_world_content import load_living_world_content
from backend.models import NPCMemory, NPCRow, Room, TriggerFiring, WorldState
from backend.seeds import get_or_seed_default_room


async def _seed_living_world(session):
    await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())


def _single_trigger_content(trigger):
    return replace(
        load_living_world_content(),
        triggers={trigger["id"]: trigger},
    )


def _dawn_memory_trigger(trigger_id: str, npc_id: str):
    return {
        "id": trigger_id,
        "kind": "story",
        "participants": [npc_id],
        "window": {
            "opens_day": 0,
            "closes_day": None,
            "cooldown_minutes": 0,
            "max_firings": 1,
        },
        "conditions": [{"kind": "day_phase", "phases": ["dawn"]}],
        "effects": [{
            "kind": "remember",
            "npc_id": npc_id,
            "summary": f"{trigger_id} survived its original temporal window.",
            "importance": 6,
            "tags": ["person", "testimony"],
        }],
        "conversation": None,
        "missed_consequences": [],
        "aftermath_clues": [],
    }


async def test_failed_trigger_transaction_retries_the_committed_clock_interval(
    session,
    monkeypatch,
):
    await _seed_living_world(session)
    trigger = _dawn_memory_trigger(
        "reliability-crash-window",
        "fen-alder",
    )
    content = _single_trigger_content(trigger)

    # The producer clock commits first, exactly as it does in the app ticker.
    state = await session.get(WorldState, 1)
    assert state.variables["authored_triggers_through_minute"] == 0
    state.world_minute = 600
    await session.commit()

    original_apply = trigger_runtime._apply_effects

    async def fail_after_clock_commit(*args, **kwargs):
        raise RuntimeError("injected trigger failure")

    monkeypatch.setattr(
        trigger_runtime,
        "_apply_effects",
        fail_after_clock_commit,
    )
    with pytest.raises(RuntimeError, match="injected trigger failure"):
        await advance_authored_triggers(
            session,
            from_minute=0,
            to_minute=600,
            active_room_ids=(),
            content=content,
        )

    await session.refresh(state)
    assert state.world_minute == 600
    assert state.variables["authored_triggers_through_minute"] == 0

    # The next ticker only knows the new point. The durable checkpoint restores
    # the failed 0..600 interval, in which dawn existed.
    monkeypatch.setattr(
        trigger_runtime,
        "_apply_effects",
        original_apply,
    )
    retried = await advance_authored_triggers(
        session,
        from_minute=600,
        to_minute=600,
        active_room_ids=(),
        content=content,
    )
    assert retried.fired == 1

    replay = await advance_authored_triggers(
        session,
        from_minute=600,
        to_minute=600,
        active_room_ids=(),
        content=content,
    )
    assert replay.fired == 0
    firings = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == trigger["id"],
        )
    )).scalars().all()
    memories = (await session.execute(
        select(NPCMemory).where(
            NPCMemory.summary
            == "reliability-crash-window survived its original temporal window.",
        )
    )).scalars().all()
    await session.refresh(state)
    assert len(firings) == 1
    assert len(memories) == 1
    assert state.variables["authored_triggers_through_minute"] == 600


async def test_active_room_deferral_preserves_the_original_temporal_window(
    session,
):
    await _seed_living_world(session)
    trigger = _dawn_memory_trigger(
        "reliability-active-window",
        "fen-alder",
    )
    content = _single_trigger_content(trigger)
    fen = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "fen-alder")
    )).scalar_one()

    deferred = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=600,
        active_room_ids=(fen.room_id,),
        content=content,
    )
    assert deferred.fired == 0
    state = await session.get(WorldState, 1)
    assert state.variables["authored_triggers_through_minute"] == 600
    assert state.variables["authored_trigger_deferred_from"] == {
        trigger["id"]: 0,
    }

    # Minute 600 is daytime, so this only fires if the deferred dawn interval
    # survived while the in-memory room was authoritative.
    resumed = await advance_authored_triggers(
        session,
        from_minute=600,
        to_minute=600,
        active_room_ids=(),
        content=content,
    )
    assert resumed.fired == 1
    await session.refresh(state)
    assert "authored_trigger_deferred_from" not in state.variables


async def test_missed_consequence_cannot_move_an_actor_from_an_active_room(
    session,
):
    await _seed_living_world(session)
    fen = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "fen-alder")
    )).scalar_one()
    vesna = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "vesna-korr")
    )).scalar_one()
    destination = (await session.execute(
        select(Room).where(Room.content_id == "drazna_lantern_quays")
    )).scalar_one()
    assert fen.room_id != vesna.room_id
    assert fen.room_id != destination.id
    origin_id = fen.room_id

    trigger = {
        "id": "reliability-active-missed-branch",
        "kind": "story",
        "participants": ["vesna-korr"],
        "window": {
            "opens_day": 0,
            "closes_day": 0,
            "cooldown_minutes": 0,
            "max_firings": 1,
        },
        "conditions": [{
            "kind": "npc_at",
            "npc_id": "vesna-korr",
            "location_id": "oakrun_crossroads",
        }],
        "effects": [],
        "conversation": None,
        "missed_consequences": [{
            "kind": "board_carriage",
            "npc_id": "fen-alder",
            "carriage_id": "grey-heron",
            "destination_location_id": "drazna_lantern_quays",
        }],
        "aftermath_clues": [],
    }
    content = _single_trigger_content(trigger)

    protected = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=1440,
        active_room_ids=(origin_id,),
        content=content,
    )
    await session.refresh(fen)
    assert protected.missed == 0
    assert fen.room_id == origin_id
    assert (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == trigger["id"],
        )
    )).scalar_one_or_none() is None

    resolved = await advance_authored_triggers(
        session,
        from_minute=1440,
        to_minute=1440,
        active_room_ids=(),
        content=content,
    )
    await session.refresh(fen)
    assert resolved.missed == 1
    assert fen.room_id == destination.id


async def test_catchup_crossing_window_checks_closing_conditions_before_miss(
    session,
):
    await _seed_living_world(session)
    result = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=8 * 1440,
        active_room_ids=(),
    )
    assert result.fired > 0

    firing = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "vasko-low-water-return",
        )
    )).scalar_one()
    assert firing.outcome == "applied"
    assert firing.fired_at_minute == 8 * 1440 - 1


def test_carriage_arrival_is_stop_specific_and_includes_route_time():
    content = load_living_world_content()
    grey_heron = content.carriages["grey-heron"]

    sunday_drazna_departure = 6 * 1440 + 300
    assert not trigger_runtime._carriage_in_interval(
        grey_heron,
        content.routes,
        "oakrun_pilgrims_hollow",
        sunday_drazna_departure,
        sunday_drazna_departure,
    )

    # Reverse itinerary: Glasswater (1080), Hollowmere layover (180), then
    # Unharvested Miles (540). The arrival crosses into Monday.
    monday_oakrun_arrival = 7 * 1440 + 660
    assert trigger_runtime._carriage_in_interval(
        grey_heron,
        content.routes,
        "oakrun_pilgrims_hollow",
        monday_oakrun_arrival,
        monday_oakrun_arrival,
    )


def test_carriage_arrival_resolves_an_intermediate_stop():
    content = load_living_world_content()
    mudwheel = content.carriages["mudwheel"]
    tuesday_departure = 1440 + 480
    tuesday_high_crown_arrival = 1440 + 525

    assert not trigger_runtime._carriage_in_interval(
        mudwheel,
        content.routes,
        "drazna_high_crown",
        tuesday_departure,
        tuesday_departure,
    )
    assert trigger_runtime._carriage_in_interval(
        mudwheel,
        content.routes,
        "drazna_high_crown",
        tuesday_high_crown_arrival,
        tuesday_high_crown_arrival,
    )
