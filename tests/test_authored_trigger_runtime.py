from sqlalchemy import select

from backend.living_world.player_knowledge import world_sync
from backend.living_world.service import LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.models import (
    NPCMemory,
    NPCRow,
    PlayerRow,
    Room,
    TriggerFiring,
    WorldEvent,
)
from backend.seeds import get_or_seed_default_room


async def _seed_beliefs(session):
    await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())


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
