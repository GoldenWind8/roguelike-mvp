from dataclasses import replace

from sqlalchemy import delete
from sqlalchemy import select

from backend.living_world import store
from backend.living_world.player_knowledge import world_sync
from backend.living_world.service import LivingWorldConfig, LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.living_world_content import load_living_world_content
from backend.models import (
    NPCGoal,
    NPCMemory,
    NPCRow,
    PlayerRow,
    Room,
    ScheduledWorldEvent,
    TriggerFiring,
    WorldEvent,
    WorldFact,
)
from backend.seeds import get_or_seed_default_room


async def _seed_beliefs(session):
    await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())


def _story(trigger_id, npc_id, conditions, effects):
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
        "conditions": conditions,
        "effects": effects,
        "conversation": None,
        "missed_consequences": [],
        "aftermath_clues": [],
    }


async def test_story_turn_uses_authored_local_chronicle_summary(session):
    await _seed_beliefs(session)
    base = load_living_world_content()
    trigger = _story(
        "test-authored-summary",
        "nera-bell",
        [{"kind": "fact_absent", "fact_key": "test-authored-summary"}],
        [{
            "kind": "set_fact",
            "fact_key": "test-authored-summary",
            "subject_id": "nera-bell",
            "predicate": "visible_trace",
            "value": {"state": "present"},
        }],
    )
    trigger["chronicle_summary"] = (
        "A salt outline remains where the public comparison tablet stood."
    )
    trigger["chronicle_location_id"] = "drazna_tablet_vault"
    content = replace(base, triggers={trigger["id"]: trigger})
    rooms = await store.room_id_by_content(session)
    nera = await store.npc_by_content_id(session, "nera-bell")
    nera.room_id = rooms["drazna_high_crown"]

    deferred = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=1,
        active_room_ids=(rooms["drazna_tablet_vault"],),
        content=content,
    )
    assert deferred.fired == 0

    result = await advance_authored_triggers(
        session,
        from_minute=1,
        to_minute=2,
        active_room_ids=(),
        content=content,
    )
    assert result.fired == 1
    event = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.dedupe_key
            == "trigger-event:test-authored-summary:1"
        )
    )).scalar_one()
    assert event.summary == trigger["chronicle_summary"]
    assert event.room_id == rooms["drazna_tablet_vault"]
    assert event.visibility == "public_aftermath"
    assert event.payload["chronicle_location_id"] == "drazna_tablet_vault"


async def test_real_schedules_cannot_misplace_fixed_drazna_story_traces(
    session,
):
    await get_or_seed_default_room(session)
    service = LivingWorldService(config=LivingWorldConfig(
        game_minutes_per_real_minute=1440,
        catchup_cap_minutes=1440,
        max_events_per_advance=5000,
        max_conversations_per_advance=0,
    ))
    for day in range(8):
        result = await service.advance(session, day * 60, ())
        await advance_authored_triggers(
            session,
            from_minute=result.from_minute,
            to_minute=result.to_minute,
            active_room_ids=(),
        )

    room_ids = await store.room_id_by_content(session)
    expected_locations = {
        "sima-bridge-injury": "drazna_walking_ward",
        "teo-sells-low-lantern-list": "drazna_reed_market",
        "nera-tablet-theft": "drazna_tablet_vault",
    }
    for trigger_id, location_id in expected_locations.items():
        firing = (await session.execute(
            select(TriggerFiring).where(
                TriggerFiring.trigger_id == trigger_id,
                TriggerFiring.outcome == "applied",
            )
        )).scalar_one()
        event = await session.get(WorldEvent, firing.event_id)
        assert event is not None
        assert event.room_id == room_ids[location_id]
        assert event.payload["chronicle_location_id"] == location_id


async def test_in_transit_npc_is_not_at_a_room_for_authored_conditions(session):
    await _seed_beliefs(session)
    base = load_living_world_content()
    nera = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "nera-bell")
    )).scalar_one()
    luka = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "luka-nen")
    )).scalar_one()
    house = (await session.execute(
        select(Room).where(Room.content_id == "drazna_house_of_names")
    )).scalar_one()
    vault = (await session.execute(
        select(Room).where(Room.content_id == "drazna_tablet_vault")
    )).scalar_one()
    nera.room_id = house.id
    luka.room_id = house.id
    nera.x, nera.y = 2, 2
    luka.x, luka.y = 3, 2
    await session.execute(delete(ScheduledWorldEvent).where(
        ScheduledWorldEvent.actor_id == nera.content_id,
        ScheduledWorldEvent.kind == "npc_arrive_room",
    ))
    edgewise = ScheduledWorldEvent(
        dedupe_key="journey:test-nera-between-rooms",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=nera.content_id,
        room_id=house.id,
        payload={
            "route_room_ids": [house.id, vault.id],
            "step_index": 1,
            "from_room_id": house.id,
            "to_room_id": vault.id,
            "final_room_id": vault.id,
        },
    )
    coalesced = ScheduledWorldEvent(
        dedupe_key="journey:test-luka-between-rooms",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=luka.content_id,
        room_id=house.id,
        payload={
            "route_room_ids": [house.id, vault.id],
            "step_index": 1,
            "from_room_id": house.id,
            "to_room_id": vault.id,
            "final_room_id": vault.id,
            "coalesced_schedule": True,
        },
    )
    session.add_all((edgewise, coalesced))
    await session.commit()

    trigger = _story(
        "test-in-transit-location",
        nera.content_id,
        [
            {
                "kind": "co_located",
                "npc_ids": [nera.content_id, luka.content_id],
            },
            {
                "kind": "npc_at",
                "npc_id": nera.content_id,
                "location_id": house.content_id,
            },
        ],
        [{
            "kind": "set_fact",
            "fact_key": "test-in-transit-location",
            "subject_id": nera.content_id,
            "predicate": "physically_present",
            "value": {"state": "present"},
        }],
    )
    trigger["participants"] = [nera.content_id, luka.content_id]
    content = replace(base, triggers={trigger["id"]: trigger})

    blocked = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=1,
        active_room_ids=(),
        content=content,
    )
    assert blocked.fired == 0

    edgewise.status = "cancelled"
    await session.commit()
    still_blocked = await advance_authored_triggers(
        session,
        from_minute=1,
        to_minute=2,
        active_room_ids=(),
        content=content,
    )
    assert still_blocked.fired == 0

    coalesced.status = "cancelled"
    await session.commit()
    resumed = await advance_authored_triggers(
        session,
        from_minute=2,
        to_minute=3,
        active_room_ids=(),
        content=content,
    )
    assert resumed.fired == 1


async def test_dead_npcs_cannot_converse_take_directions_or_board_carriages(
    session,
):
    await _seed_beliefs(session)
    base = load_living_world_content()
    rooms = await store.room_id_by_content(session)
    nera = await store.npc_by_content_id(session, "nera-bell")
    luka = await store.npc_by_content_id(session, "luka-nen")
    assert nera is not None and luka is not None
    nera.room_id = luka.room_id = rooms["drazna_house_of_names"]
    nera.hp = 0
    nera.is_alive = False
    origin = nera.room_id

    direction = _story(
        "test-dead-direction",
        nera.content_id,
        [{"kind": "fact_absent", "fact_key": "test-dead-direction"}],
        [{
            "kind": "set_direction",
            "npc_id": nera.content_id,
            "location_id": "drazna_tablet_vault",
            "reason": "A dead archivist cannot accept a new route.",
        }],
    )
    boarding = _story(
        "test-dead-boarding",
        nera.content_id,
        [{"kind": "fact_absent", "fact_key": "test-dead-boarding"}],
        [{
            "kind": "board_carriage",
            "npc_id": nera.content_id,
            "destination_location_id": "drazna_lantern_quays",
            "carriage_id": "grey-heron",
        }],
    )
    rumor = _story(
        "test-dead-rumor",
        nera.content_id,
        [{"kind": "fact_absent", "fact_key": "test-dead-rumor"}],
        [{
            "kind": "share_rumor",
            "speaker_npc_id": nera.content_id,
            "listener_npc_id": luka.content_id,
            "rumor_id": "drazna-first-record",
        }],
    )
    conversation = {
        "id": "test-dead-conversation",
        "kind": "conversation",
        "participants": [nera.content_id, luka.content_id],
        "window": {
            "opens_day": 0,
            "closes_day": None,
            "cooldown_minutes": 0,
            "max_firings": 1,
        },
        "conditions": [{
            "kind": "co_located",
            "npc_ids": [nera.content_id, luka.content_id],
        }],
        "effects": [{
            "kind": "relationship_shift",
            "from_npc_id": luka.content_id,
            "to_npc_id": nera.content_id,
            "axis": "trust",
            "delta": 1,
        }],
        "conversation": {
            "opening_speaker_npc_id": luka.content_id,
            "opening_line": "This line must never be spoken.",
            "mode": "continuous",
            "max_turns": 2,
            "topics": ["archive"],
            "stop_when": ["separated"],
            "followup_trigger_ids": [],
        },
        "missed_consequences": [],
        "aftermath_clues": [],
    }
    triggers = {
        trigger["id"]: trigger
        for trigger in (direction, boarding, rumor, conversation)
    }
    content = replace(base, triggers=triggers)

    result = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=1,
        active_room_ids=(),
        content=content,
    )

    assert result.fired == 0
    assert nera.room_id == origin
    assert (await session.execute(
        select(TriggerFiring).where(TriggerFiring.trigger_id.in_(triggers))
    )).scalars().all() == []
    assert (await session.execute(
        select(NPCGoal).where(
            NPCGoal.goal_key.like("trigger-direction:test-dead-direction%")
        )
    )).scalars().all() == []


async def test_recorded_death_cancels_pending_actor_and_listener_actions(
    session,
):
    await _seed_beliefs(session)
    nera = await store.npc_by_content_id(session, "nera-bell")
    luka = await store.npc_by_content_id(session, "luka-nen")
    olek = await store.npc_by_content_id(session, "olek-var")
    assert nera is not None and luka is not None and olek is not None
    actor_action = ScheduledWorldEvent(
        dedupe_key="test-death-cancels-actor",
        kind="npc_arrive_room",
        due_minute=600,
        priority=10,
        actor_id=nera.content_id,
        room_id=nera.room_id,
        payload={"to_room_id": luka.room_id},
    )
    listener_action = ScheduledWorldEvent(
        dedupe_key="test-death-cancels-listener",
        kind="npc_conversation",
        due_minute=601,
        priority=10,
        actor_id=luka.content_id,
        target_id=nera.content_id,
        room_id=nera.room_id,
        payload={},
    )
    unrelated = ScheduledWorldEvent(
        dedupe_key="test-death-keeps-unrelated",
        kind="npc_conversation",
        due_minute=602,
        priority=10,
        actor_id=luka.content_id,
        target_id=olek.content_id,
        room_id=luka.room_id,
        payload={},
    )
    session.add_all((actor_action, listener_action, unrelated))
    await session.flush()

    nera.hp = 0
    nera.is_alive = False
    await store.record_npc_death(
        session,
        npc_content_id=nera.content_id,
        npc_name=nera.name,
        max_hp=nera.max_hp,
        room_id=nera.room_id,
        world_minute=500,
        summary="An archivist died before the appointed meeting.",
        source="test",
    )
    await session.flush()

    assert actor_action.status == listener_action.status == "cancelled"
    assert actor_action.resolved_at_minute == 500
    assert listener_action.resolved_at_minute == 500
    assert actor_action.last_error == listener_action.last_error == (
        "NPC died before this action resolved"
    )
    assert unrelated.status == "pending"


async def test_vasko_ledger_conversation_follows_a_visible_return(session):
    await _seed_beliefs(session)

    await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=31 * 1440,
        active_room_ids=(),
    )

    return_firing = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "vasko-returns-with-ledger"
        )
    )).scalar_one()
    conversation_firing = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "olek-vasko-ledger-return"
        )
    )).scalar_one()
    assert return_firing.outcome == "applied"
    assert conversation_firing.outcome == "applied"
    assert return_firing.fired_at_minute <= conversation_firing.fired_at_minute

    conversation_event = await session.get(
        WorldEvent,
        conversation_firing.event_id,
    )
    assert conversation_event is not None
    assert conversation_event.visibility == "private"


async def test_authored_conversation_fires_from_real_conditions(session):
    await _seed_beliefs(session)
    result = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=19 * 60,
        active_room_ids=(),
    )
    assert result.fired > 0

    firing = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "elowen-rowan-dated-rumor",
            TriggerFiring.outcome == "applied",
        )
    )).scalar_one()
    assert firing.ordinal == 1
    remembered = (await session.execute(
        select(NPCMemory).where(
            NPCMemory.npc_content_id == "rowan-oakrun-courier",
            NPCMemory.summary.contains("first recorded"),
        )
    )).scalar_one()
    assert remembered.importance == 5
    shared = (await session.execute(
        select(NPCMemory).where(
            NPCMemory.npc_content_id == "elowen-wayfarers-rest",
            NPCMemory.payload["rumor_id"].as_string() == "drazna-first-record",
        )
    )).scalars().all()
    assert shared
    event = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.dedupe_key == "trigger-event:elowen-rowan-dated-rumor:1"
        )
    )).scalar_one()
    assert event.kind == "authored_conversation"
    assert event.visibility == "private"


async def test_active_room_defers_authored_trigger_authority(session):
    room = await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())
    result = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=19 * 60,
        active_room_ids=(room.id,),
    )
    firing = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "elowen-rowan-dated-rumor"
        )
    )).scalars().first()
    assert firing is None
    assert result.fired >= 0


async def test_wound_is_bounded_and_same_minute_warning_cannot_enable_death(
    session,
):
    await _seed_beliefs(session)
    await session.execute(delete(NPCMemory).where(
        NPCMemory.npc_content_id == "ilya-sorn"
    ))
    await session.commit()
    base = load_living_world_content()
    wound = _story(
        "test-ilya-wound",
        "ilya-sorn",
        [{"kind": "npc_alive", "npc_id": "ilya-sorn", "value": True}],
        [{
            "kind": "wound_npc",
            "npc_id": "ilya-sorn",
            "damage": 1000,
            "summary": "Ilya was crushed beneath a failing pressure brace.",
        }],
    )
    death = _story(
        "test-ilya-death",
        "ilya-sorn",
        [
            {"kind": "npc_alive", "npc_id": "ilya-sorn", "value": True},
            {"kind": "npc_health_at_most", "npc_id": "ilya-sorn", "hp": 1},
        ],
        [{
            "kind": "kill_npc",
            "npc_id": "ilya-sorn",
            "summary": "The lower sluice took Ilya before help reached him.",
        }],
    )
    content = replace(base, triggers={
        wound["id"]: wound,
        death["id"]: death,
    })

    first = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=10,
        active_room_ids=(),
        content=content,
    )
    ilya = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "ilya-sorn")
    )).scalar_one()
    assert first.fired == 1
    assert (ilya.hp, ilya.is_alive) == (1, True)
    assert (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "test-ilya-death"
        )
    )).scalars().first() is None

    second = await advance_authored_triggers(
        session,
        from_minute=10,
        to_minute=11,
        active_room_ids=(),
        content=content,
    )
    await session.refresh(ilya)
    assert second.fired == 1
    assert (ilya.hp, ilya.is_alive) == (0, False)
    fate = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "npc-fate:ilya-sorn"
        )
    )).scalar_one()
    assert fate.value["is_alive"] is False
    assert (await session.execute(
        select(WorldEvent).where(
            WorldEvent.dedupe_key == "npc-death:ilya-sorn"
        )
    )).scalar_one()


async def test_fact_conditions_and_state_effects_compose_in_one_pass(session):
    await _seed_beliefs(session)
    base = load_living_world_content()
    fact_key = "test-alin-decision"
    establish = _story(
        "test-alin-establish",
        "alin-vey",
        [{"kind": "fact_absent", "fact_key": fact_key}],
        [{
            "kind": "set_fact",
            "fact_key": fact_key,
            "subject_id": "alin-vey",
            "predicate": "decision",
            "value": {"choice": "publish"},
        }],
    )
    consequence = _story(
        "test-alin-consequence",
        "alin-vey",
        [
            {"kind": "fact_exists", "fact_key": fact_key},
            {
                "kind": "fact_equals",
                "fact_key": fact_key,
                "value": {"choice": "publish"},
            },
        ],
        [
            {
                "kind": "set_disposition",
                "npc_id": "alin-vey",
                "disposition": "friendly",
            },
            {
                "kind": "set_goal_status",
                "npc_id": "alin-vey",
                "goal_id": "publish-flood-record",
                "status": "completed",
                "reason": "Alin released the unabridged flood record.",
            },
        ],
    )
    content = replace(base, triggers={
        establish["id"]: establish,
        consequence["id"]: consequence,
    })

    result = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=20,
        active_room_ids=(),
        content=content,
    )

    assert result.fired == 2
    alin = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "alin-vey")
    )).scalar_one()
    assert alin.disposition == "friendly"
    goal = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == "alin-vey",
            NPCGoal.goal_key == "publish-flood-record",
        )
    )).scalar_one()
    assert goal.status == "completed"
    fact = (await session.execute(
        select(WorldFact).where(WorldFact.fact_key == fact_key)
    )).scalar_one()
    assert fact.value == {"choice": "publish"}


async def test_disappearance_respects_destination_authority_and_leaves_evidence(
    session,
):
    await _seed_beliefs(session)
    base = load_living_world_content()
    trigger = _story(
        "test-pava-disappears",
        "pava-mirek",
        [{"kind": "npc_alive", "npc_id": "pava-mirek", "value": True}],
        [{
            "kind": "disappear_npc",
            "npc_id": "pava-mirek",
            "location_id": "drazna_undertide",
            "reason": "Pava left a snapped chalk line and went below alone.",
        }],
    )
    content = replace(base, triggers={trigger["id"]: trigger})
    rooms = await store.room_id_by_content(session)
    pava = await store.npc_by_content_id(session, "pava-mirek")
    origin = pava.room_id

    deferred = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=30,
        active_room_ids=(rooms["drazna_undertide"],),
        content=content,
    )
    await session.refresh(pava)
    assert deferred.fired == 0
    assert pava.room_id == origin

    applied = await advance_authored_triggers(
        session,
        from_minute=30,
        to_minute=31,
        active_room_ids=(),
        content=content,
    )
    await session.refresh(pava)
    assert applied.fired == 1
    assert pava.room_id == rooms["drazna_undertide"]
    assert (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "evidence_left",
            WorldEvent.room_id == origin,
            WorldEvent.summary
            == "An abandoned place holds signs of a hurried departure.",
        )
    )).scalar_one()
    whereabouts = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "npc-whereabouts:pava-mirek"
        )
    )).scalar_one()
    assert whereabouts.value["disappeared"] is True


async def test_missed_story_window_is_permanent_and_leaves_findable_evidence(session):
    await _seed_beliefs(session)
    vasko = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "vasko-mirek")
    )).scalar_one()
    high_crown = (await session.execute(
        select(Room).where(Room.content_id == "drazna_high_crown")
    )).scalar_one()
    # Remove the one condition that could save this opportunity.
    vasko.room_id = high_crown.id
    vasko.x, vasko.y = 5, 5
    await session.commit()

    result = await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=8 * 1440,
        active_room_ids=(),
    )
    assert result.missed > 0
    missed = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "vasko-low-water-return",
            TriggerFiring.outcome == "missed",
        )
    )).scalar_one()
    assert missed.fired_at_minute == 8 * 1440 - 1

    evidence = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "evidence_left",
            WorldEvent.summary.contains("Vasko"),
        )
    )).scalar_one()
    assert evidence.visibility == "discoverable"
    authored_clue = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.dedupe_key
            == "trigger-aftermath:vasko-low-water-return:0"
        )
    )).scalar_one()
    assert authored_clue.visibility == "discoverable"
    assert authored_clue.payload["expires_at_minute"] == (
        missed.fired_at_minute + 90 * 1440
    )

    player = PlayerRow(
        id="player_aftermath",
        username="aftermath",
        password_hash="unused",
        room_id=evidence.room_id,
        hp=50,
    )
    session.add(player)
    await session.commit()
    payload = await world_sync(
        session,
        player_id=player.id,
        current_room_id=evidence.room_id,
    )
    assert any(
        entry["provenance"] == "found" and "Vasko" in entry["body"]
        for entry in payload["chronicle"]
    )

    # A later pass cannot resurrect the missed branch.
    await advance_authored_triggers(
        session,
        from_minute=8 * 1440,
        to_minute=12 * 1440,
        active_room_ids=(),
    )
    rows = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "vasko-low-water-return"
        )
    )).scalars().all()
    assert [(row.outcome, row.ordinal) for row in rows] == [("missed", 1)]
