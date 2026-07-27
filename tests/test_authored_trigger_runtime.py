from dataclasses import replace

from sqlalchemy import delete
from sqlalchemy import select

from backend.living_world import store
from backend.living_world.player_knowledge import world_sync
from backend.living_world.service import LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.living_world_content import load_living_world_content
from backend.models import (
    NPCGoal,
    NPCMemory,
    NPCRow,
    PlayerRow,
    Room,
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
            WorldEvent.summary.contains("chalk line"),
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
