from sqlalchemy import select

from backend.living_world.player_knowledge import (
    dialogue_memory_context,
    record_player_conversation,
    world_sync,
)
from backend.models import NPCMemory, NPCRelationship, NPCRow, PlayerRow, Room
from backend.seeds import get_or_seed_default_room


async def _player_and_basil(session):
    room = await get_or_seed_default_room(session)
    player = PlayerRow(
        id="player_world_reader",
        username="world-reader",
        password_hash="unused",
        room_id=room.id,
        x=12,
        y=10,
        hp=50,
    )
    session.add(player)
    await session.commit()
    basil = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "basil-oakrun")
    )).scalar_one()
    return room, player, basil


async def test_world_sync_only_shows_people_the_player_has_observed(session):
    room, player, _basil = await _player_and_basil(session)
    payload = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )

    assert payload["type"] == "world_sync"
    assert payload["time"]["label"] == "Day 1, deep night"
    assert payload["rumors"] == []
    names = {person["name"] for person in payload["known_people"]}
    assert names == {
        "Basil",
        "Elowen Pike",
        "Tom Weller",
        "Hester Vale",
        "Rowan Hale",
        "Alys Ward",
    }
    assert all(person["availability"] == "present" for person in payload["known_people"])
    assert all(person["dialogue_topics"] == [] for person in payload["known_people"])


async def test_dialogue_creates_memory_relationship_rumor_and_chronicle(session):
    room, player, basil = await _player_and_basil(session)
    await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )

    learned = await record_player_conversation(
        session,
        player_id=player.id,
        player_name="Reader",
        npc_content_id=basil.content_id,
        npc_name=basil.name,
        room_id=room.id,
        player_text="What do you know about the rot?",
        npc_text="Symptoms tell the truth before rumors do.",
        world_minute=400,
    )
    await session.commit()
    assert learned is not None
    assert learned.knowledge_key in {
        "black-silt-erases-names",
        "medicine-stops-rot",
    }

    memories = (await session.execute(
        select(NPCMemory).where(NPCMemory.npc_content_id == basil.content_id)
    )).scalars().all()
    assert len(memories) == 1
    assert "Reader said" in memories[0].summary

    relation = (await session.execute(
        select(NPCRelationship).where(
            NPCRelationship.source_npc_content_id == basil.content_id,
            NPCRelationship.target_kind == "player",
            NPCRelationship.target_id == player.id,
        )
    )).scalar_one()
    assert relation.familiarity == 4

    context = await dialogue_memory_context(
        session,
        npc_content_id=basil.content_id,
        player_id=player.id,
        text="Do you remember what I asked about rot?",
        world_minute=410,
    )
    assert context["memories"]
    assert "Reader said" in context["memories"][0]

    payload = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    assert len(payload["rumors"]) == 1
    assert payload["rumors"][0]["source"] == "Basil"
    basil_view = next(
        person for person in payload["known_people"]
        if person["world_id"] == basil.content_id
    )
    assert [topic["id"] for topic in basil_view["dialogue_topics"]] == [
        learned.knowledge_key
    ]
    assert any(entry["title"] == "Words exchanged" for entry in payload["chronicle"])


async def test_world_sync_never_reveals_an_observed_persons_offscreen_truth(session):
    room, player, basil = await _player_and_basil(session)
    first = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    assert next(
        person for person in first["known_people"]
        if person["world_id"] == basil.content_id
    )["relationship"] == "unfamiliar"

    distant_room = (await session.execute(
        select(Room).where(Room.id != room.id).order_by(Room.id)
    )).scalars().first()
    basil.room_id = distant_room.id
    basil.is_alive = False
    session.add(NPCRelationship(
        source_npc_content_id=basil.content_id,
        target_kind="player",
        target_id=player.id,
        familiarity=100,
        trust=100,
        affinity=100,
    ))
    await session.commit()

    later = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    known = next(
        person for person in later["known_people"]
        if person["world_id"] == basil.content_id
    )
    assert known["availability"] == "away"
    assert known["activity"] is None
    assert known["relationship"] == "unfamiliar"
