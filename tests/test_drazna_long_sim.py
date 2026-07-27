from sqlalchemy import select

from backend.living_world.player_knowledge import record_object_discovery
from backend.living_world.service import LivingWorldConfig, LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.models import (
    NPCGoal,
    NPCMemory,
    NPCRow,
    PlayerRow,
    Room,
    TriggerFiring,
    WorldFact,
)
from backend.object_defs import get_object_definition
from backend.seeds import get_or_seed_default_room
from backend.situation_defs import get_situation
from backend.situation_store import resolve_situation_choice


async def test_thirty_day_absent_player_simulation_reaches_coherent_climax(
    session,
):
    await get_or_seed_default_room(session)
    service = LivingWorldService(config=LivingWorldConfig(
        game_minutes_per_real_minute=1440,
        catchup_cap_minutes=1440,
        max_events_per_advance=5000,
        # Thirty days still exercise roughly 240 ordinary conversations,
        # while keeping this content audit bounded despite the separately
        # tracked superlinear history-scan cost at the production cap of 32.
        max_conversations_per_advance=8,
    ))

    for day in range(31):
        result = await service.advance(session, day * 60, ())
        await advance_authored_triggers(
            session,
            from_minute=result.from_minute,
            to_minute=result.to_minute,
            active_room_ids=(),
        )

    firings = {
        firing.trigger_id: firing.outcome
        for firing in (await session.execute(select(TriggerFiring))).scalars()
    }
    expected_story_chain = {
        "drina-teo-passenger-list",
        "mudwheel-names-at-ember",
        "rada-sima-pressure-warning",
        "teo-sells-low-lantern-list",
        "sima-bridge-injury",
        "sima-joins-undertide-descent",
        "luka-confronts-teo",
        "luka-testifies-names",
        "undertide-expedition-launch",
        "nera-tablet-theft",
        "vasko-returns-with-ledger",
        "mara-alin-hearing-outcome",
        "gate-seven-climax",
        "odran-cadence-pacified",
        "gate-seven-pacified-aftermath",
    }
    assert expected_story_chain <= set(firings)
    assert {
        trigger_id: firings[trigger_id]
        for trigger_id in expected_story_chain
    } == {
        trigger_id: "applied"
        for trigger_id in expected_story_chain
    }
    assert {
        "gate-seven-unanswered-flood",
        "gate-seven-cadence-expired",
        "gate-seven-flood-aftermath",
        "gate-seven-killed-aftermath",
    }.isdisjoint(firings)

    facts = {
        fact.fact_key: fact.value
        for fact in (await session.execute(
            select(WorldFact).where(WorldFact.fact_key.like("drazna.%"))
        )).scalars()
    }
    assert facts["drazna.undertide_expedition"]["state"] == "launched"
    assert facts["drazna.vasko_return"]["state"] == "returned"
    assert facts["drazna.crown_hearing"]["state"] == "opened"
    assert facts["drazna.gate_seven_resolution"] == {
        "state": "pacified",
        "gate": "vented",
        "names_spoken": 14,
    }

    odran = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "odran-third-bell")
    )).scalar_one()
    luka = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "luka-nen")
    )).scalar_one()
    assert (odran.is_alive, odran.disposition) == (True, "neutral")
    assert luka.is_alive

    rada_goal = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == "rada-velic",
            NPCGoal.goal_key == "stabilize-gate-seven",
        )
    )).scalar_one()
    assert rada_goal.status == "completed"


async def test_thirty_day_engaged_resolution_is_not_overridden_offscreen(
    session,
):
    await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())
    gate = (await session.execute(
        select(Room).where(Room.content_id == "drazna_gate_seven")
    )).scalar_one()
    player = PlayerRow(
        id="drazna_long_engineer",
        username="drazna_long_engineer",
        password_hash="unused",
        room_id=gate.id,
        x=4,
        y=8,
        hp=100,
    )
    session.add(player)
    for minute, object_type in (
        (100, "drazna_pressure_gauge"),
        (101, "drazna_crown_flood_order"),
    ):
        discovery = get_object_definition(object_type).discovery
        assert discovery is not None
        await record_object_discovery(
            session,
            player_id=player.id,
            room_id=gate.id,
            object_id=f"long-sim:{object_type}",
            discovery=discovery,
            world_minute=minute,
        )
    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None
    resolution = await resolve_situation_choice(
        session,
        definition=definition,
        choice_id="brace-the-counterpressure",
        player_id=player.id,
        room_id=gate.id,
        world_minute=120,
    )
    assert resolution.outcome == "contained"
    await session.commit()

    # While the player remains in Gate Seven, off-screen trigger authority
    # must defer. Once they leave, only the contained aftermath may resolve.
    await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=30 * 1440,
        active_room_ids=(gate.id,),
    )
    await advance_authored_triggers(
        session,
        from_minute=30 * 1440,
        to_minute=30 * 1440,
        active_room_ids=(),
    )

    firings = {
        firing.trigger_id: firing.outcome
        for firing in (await session.execute(select(TriggerFiring))).scalars()
    }
    assert firings["gate-seven-contained-aftermath"] == "applied"
    assert {
        "gate-seven-climax",
        "gate-seven-unanswered-flood",
        "odran-cadence-pacified",
        "gate-seven-pacified-aftermath",
        "gate-seven-cadence-expired",
        "gate-seven-killed-aftermath",
        "gate-seven-flood-aftermath",
    }.isdisjoint(firings)

    resolution_fact = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "drazna.gate_seven_resolution"
        )
    )).scalar_one()
    assert resolution_fact.value == {
        "state": "contained",
        "gate": "braced",
        "names_spoken": 0,
    }
    ward_fact = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "drazna.walking_ward_after_gate"
        )
    )).scalar_one()
    assert ward_fact.value == {"state": "stabilized", "evacuated_houses": 0}

    odran = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "odran-third-bell")
    )).scalar_one()
    rada = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "rada-velic")
    )).scalar_one()
    assert (odran.is_alive, odran.disposition) == (True, "neutral")
    assert rada.hp == 42


async def test_unanswered_engaged_cadence_floods_without_overwriting_resolution(
    session,
):
    await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())

    # Remove Rada's certainty about the fourteen-name cadence. The expedition
    # can still reach Gate Seven, but its automatic peaceful answer cannot.
    memories = (await session.execute(
        select(NPCMemory).where(NPCMemory.npc_content_id == "rada-velic")
    )).scalars()
    for memory in memories:
        if memory.payload.get("rumor_id") == "gate-seven-fourteen":
            memory.confidence = 0.0
    await session.commit()

    await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=16 * 1440,
        active_room_ids=(),
    )

    firings = {
        firing.trigger_id: firing.outcome
        for firing in (await session.execute(select(TriggerFiring))).scalars()
    }
    assert firings["gate-seven-climax"] == "applied"
    assert firings["gate-seven-cadence-expired"] == "applied"
    assert firings["gate-seven-flood-aftermath"] == "applied"
    assert {
        "odran-cadence-pacified",
        "gate-seven-pacified-aftermath",
        "gate-seven-unanswered-flood",
        "gate-seven-killed-aftermath",
    }.isdisjoint(firings)

    facts = {
        fact.fact_key: fact.value
        for fact in (await session.execute(
            select(WorldFact).where(
                WorldFact.fact_key.in_({
                    "drazna.gate_seven_resolution",
                    "drazna.gate_seven_cadence",
                    "drazna.gate_seven_climax",
                    "drazna.walking_ward_after_gate",
                })
            )
        )).scalars()
    }
    assert "drazna.gate_seven_resolution" not in facts
    assert facts["drazna.gate_seven_cadence"] == {
        "state": "failed",
        "names_spoken": 9,
    }
    assert facts["drazna.gate_seven_climax"]["state"] == "flooded"
    assert facts["drazna.walking_ward_after_gate"]["state"] == (
        "partly-collapsed"
    )

    await advance_authored_triggers(
        session,
        from_minute=16 * 1440,
        to_minute=46 * 1440,
        active_room_ids=(),
    )
    later_facts = {
        fact.fact_key: fact.value
        for fact in (await session.execute(
            select(WorldFact).where(
                WorldFact.fact_key.in_(facts)
            )
        )).scalars()
    }
    assert later_facts == facts
    later_firings = {
        firing.trigger_id: firing.outcome
        for firing in (await session.execute(select(TriggerFiring))).scalars()
    }
    gate_firing_ids = {
        trigger_id
        for trigger_id in firings
        if trigger_id.startswith("gate-seven")
        or trigger_id.startswith("odran-")
    }
    assert {
        trigger_id: later_firings[trigger_id]
        for trigger_id in gate_firing_ids
    } == {
        trigger_id: firings[trigger_id]
        for trigger_id in gate_firing_ids
    }


async def test_thirty_day_missed_undertide_branch_reaches_flooded_gate(session):
    await get_or_seed_default_room(session)
    service = LivingWorldService()
    await service.advance(session, 0, ())

    # Without Pava there is no expedition crew. This is a world-state branch,
    # not a player-facing task toggle.
    pava = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "pava-mirek")
    )).scalar_one()
    pava.hp = 0
    pava.is_alive = False
    await session.commit()

    for day in range(30):
        await advance_authored_triggers(
            session,
            from_minute=day * 1440,
            to_minute=(day + 1) * 1440,
            active_room_ids=(),
        )

    firings = {
        firing.trigger_id: firing.outcome
        for firing in (await session.execute(select(TriggerFiring))).scalars()
    }
    assert firings["undertide-expedition-launch"] == "missed"
    assert firings["luka-dry-dock-last-window"] == "applied"
    assert firings["gate-seven-unanswered-flood"] == "applied"
    assert firings["gate-seven-flood-aftermath"] == "applied"
    assert {
        "gate-seven-climax",
        "odran-cadence-pacified",
        "gate-seven-pacified-aftermath",
        "gate-seven-cadence-expired",
        "gate-seven-killed-aftermath",
    }.isdisjoint(firings)

    luka = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "luka-nen")
    )).scalar_one()
    assert (luka.hp, luka.is_alive) == (0, False)
    facts = {
        fact.fact_key: fact.value
        for fact in (await session.execute(
            select(WorldFact).where(
                WorldFact.fact_key.in_({
                    "drazna.undertide_expedition",
                    "drazna.luka_last_window",
                    "drazna.gate_seven_climax",
                    "drazna.walking_ward_after_gate",
                })
            )
        )).scalars()
    }
    assert facts["drazna.undertide_expedition"]["state"] == "missed"
    assert facts["drazna.luka_last_window"]["state"] == "dead"
    assert facts["drazna.gate_seven_climax"] == {
        "state": "flooded",
        "gate": "jammed",
        "cadence_known": True,
    }
    assert facts["drazna.walking_ward_after_gate"] == {
        "state": "partly-collapsed",
        "evacuated_houses": 3,
    }
