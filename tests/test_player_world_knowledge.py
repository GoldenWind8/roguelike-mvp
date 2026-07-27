from sqlalchemy import select

from backend.living_world.player_knowledge import (
    dialogue_memory_context,
    record_player_conversation,
    world_sync,
)
from backend.models import (
    NPCGoal,
    NPCMemory,
    NPCRelationship,
    NPCRow,
    PlayerRow,
    Room,
    ScheduledWorldEvent,
    WorldEvent,
    WorldState,
)
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


async def _set_world_minute(session, minute: int):
    world = await session.get(WorldState, 1)
    if world is None:
        world = WorldState(id=1, world_minute=minute)
        session.add(world)
    else:
        world.world_minute = minute
    await session.commit()
    return world


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


async def test_present_person_with_travel_goal_is_not_described_as_out_of_sight(
    session,
):
    room, player, basil = await _player_and_basil(session)
    session.add(NPCGoal(
        npc_content_id=basil.content_id,
        goal_key="leave-for-the-road",
        kind="travel",
        priority=99,
        status="active",
        created_at_minute=0,
        next_deliberation_minute=500,
    ))
    await session.commit()

    payload = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    known = next(
        person for person in payload["known_people"]
        if person["world_id"] == basil.content_id
    )

    assert known["availability"] == "present"
    assert known["activity"] == {
        "kind": "travelling",
        "label": "Preparing to travel",
    }


async def test_world_sync_never_observes_a_durable_row_while_npc_is_in_transit(
    session,
):
    room, player, basil = await _player_and_basil(session)
    destination = (await session.execute(
        select(Room).where(Room.id != room.id).order_by(Room.id)
    )).scalars().first()
    assert destination is not None
    first = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    assert sum(
        person["world_id"] == basil.content_id
        and person["availability"] == "present"
        for person in first["known_people"]
    ) == 1

    journey = ScheduledWorldEvent(
        dedupe_key="journey:test-basil-hidden-from-player-knowledge",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=basil.content_id,
        room_id=room.id,
        payload={
            "route_room_ids": [room.id, destination.id],
            "step_index": 1,
            "from_room_id": room.id,
            "to_room_id": destination.id,
            "final_room_id": destination.id,
            "coalesced_schedule": True,
        },
    )
    session.add(journey)
    await session.commit()

    travelling = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    [hidden] = [
        person
        for person in travelling["known_people"]
        if person["world_id"] == basil.content_id
    ]
    assert hidden["availability"] == "away"
    assert hidden["activity"] is None

    journey.status = "cancelled"
    await session.commit()
    cancelled = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    assert sum(
        person["world_id"] == basil.content_id
        and person["availability"] == "present"
        for person in cancelled["known_people"]
    ) == 1

    journey.status = "resolved"
    basil.room_id = destination.id
    basil.x, basil.y = 1, 1
    await session.commit()
    arrived = await world_sync(
        session,
        player_id=player.id,
        current_room_id=destination.id,
    )
    assert sum(
        person["world_id"] == basil.content_id
        and person["availability"] == "present"
        for person in arrived["known_people"]
    ) == 1


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


async def test_chronicle_is_durable_across_syncs(session):
    room, player, _basil = await _player_and_basil(session)
    await _set_world_minute(session, 10)
    await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )

    session.add(WorldEvent(
        kind="bells_answered",
        world_minute=20,
        room_id=room.id,
        summary="The west bell answered from an empty tower.",
        visibility="public",
    ))
    await _set_world_minute(session, 20)

    learned = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    entry = next(
        item for item in learned["chronicle"]
        if item["body"] == "The west bell answered from an empty tower."
    )
    assert entry["unread"] is True

    # A busy simulation may append hundreds of private events before the next
    # client reload. Learned entries remain in the player's retained read-set
    # even after they fall outside the global scan window.
    session.add_all([
        WorldEvent(
            kind="private_clockwork",
            world_minute=21,
            summary=f"Private simulation event {index}.",
            visibility="private",
        )
        for index in range(405)
    ])
    await _set_world_minute(session, 21)
    reloaded = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    retained = next(
        item for item in reloaded["chronicle"]
        if item["body"] == "The west bell answered from an empty tower."
    )
    assert retained["unread"] is False


async def test_unread_public_event_is_not_buried_by_private_noise(session):
    room, player, _basil = await _player_and_basil(session)
    await _set_world_minute(session, 10)
    await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )

    session.add(WorldEvent(
        kind="sluice_warning",
        world_minute=20,
        room_id=room.id,
        summary="The lower sluice bell sounded twice before dawn.",
        visibility="public",
    ))
    session.add_all([
        WorldEvent(
            kind="private_clockwork",
            world_minute=21,
            summary=f"Private simulation event {index}.",
            visibility="private",
        )
        for index in range(405)
    ])
    await _set_world_minute(session, 21)

    payload = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    warning = next(
        entry
        for entry in payload["chronicle"]
        if entry["body"] == "The lower sluice bell sounded twice before dawn."
    )
    assert warning["unread"] is True
    assert warning["provenance"] == "heard"


async def test_public_aftermath_requires_presence_not_just_a_known_person(session):
    room, player, basil = await _player_and_basil(session)
    await _set_world_minute(session, 10)
    await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    distant_room = (await session.execute(
        select(Room).where(Room.id != room.id).order_by(Room.id)
    )).scalars().first()
    session.add_all([
        WorldEvent(
            kind="known_return",
            world_minute=20,
            actor_id=basil.content_id,
            room_id=distant_room.id,
            summary="Someone reports that Basil crossed the old bridge.",
            visibility="public_aftermath",
        ),
        WorldEvent(
            kind="local_trace",
            world_minute=20,
            actor_id=basil.content_id,
            room_id=room.id,
            summary="Fresh black reeds have been arranged beneath the window.",
            visibility="public_aftermath",
        ),
        WorldEvent(
            kind="private_aftermath",
            world_minute=20,
            actor_id="unknown-stranger",
            room_id=distant_room.id,
            summary="A stranger vanished beyond the distant sluice.",
            visibility="public_aftermath",
        ),
    ])
    await _set_world_minute(session, 20)

    payload = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    by_body = {entry["body"]: entry for entry in payload["chronicle"]}
    assert "Someone reports that Basil crossed the old bridge." not in by_body
    assert "Fresh black reeds have been arranged beneath the window." in by_body
    assert by_body[
        "Fresh black reeds have been arranged beneath the window."
    ]["provenance"] == "found"
    assert by_body[
        "Fresh black reeds have been arranged beneath the window."
    ]["actor_world_ids"] == []
    assert "A stranger vanished beyond the distant sluice." not in by_body


async def test_later_visit_finds_local_aftermath_but_not_private_words(session):
    room, player, _basil = await _player_and_basil(session)
    await _set_world_minute(session, 10)
    await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    distant_room = (await session.execute(
        select(Room).where(Room.id != room.id).order_by(Room.id)
    )).scalars().first()
    session.add_all([
        WorldEvent(
            kind="collapsed_walkway",
            world_minute=20,
            room_id=distant_room.id,
            summary="A roof bridge has collapsed across the old flood line.",
            visibility="public_aftermath",
        ),
        WorldEvent(
            kind="authored_conversation",
            world_minute=20,
            room_id=distant_room.id,
            summary="Two wardens privately agreed to alter the closure roll.",
            visibility="public_aftermath",
        ),
    ])
    await _set_world_minute(session, 20)

    remote = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    assert not any(
        "roof bridge" in entry["body"]
        or "privately agreed" in entry["body"]
        for entry in remote["chronicle"]
    )

    arrived = await world_sync(
        session,
        player_id=player.id,
        current_room_id=distant_room.id,
    )
    by_body = {entry["body"]: entry for entry in arrived["chronicle"]}
    assert "A roof bridge has collapsed across the old flood line." in by_body
    assert by_body[
        "A roof bridge has collapsed across the old flood line."
    ]["provenance"] == "found"
    assert "Two wardens privately agreed to alter the closure roll." not in by_body

    departed = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    remembered = next(
        entry
        for entry in departed["chronicle"]
        if entry["body"] == "A roof bridge has collapsed across the old flood line."
    )
    assert remembered["provenance"] == "found"


async def test_people_remember_wounds_disappearances_and_witnessed_death(session):
    room, player, basil = await _player_and_basil(session)
    await _set_world_minute(session, 10)
    await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )

    basil.hp = max(1, basil.max_hp // 2)
    await _set_world_minute(session, 11)
    wounded = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    known = next(
        person for person in wounded["known_people"]
        if person["world_id"] == basil.content_id
    )
    assert known["condition"]["kind"] == "wounded"
    assert known["unread"] is True

    unchanged = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    known = next(
        person for person in unchanged["known_people"]
        if person["world_id"] == basil.content_id
    )
    assert known["unread"] is False

    distant_room = (await session.execute(
        select(Room).where(Room.id != room.id).order_by(Room.id)
    )).scalars().first()
    basil.room_id = distant_room.id
    await _set_world_minute(session, 12)
    missing = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    known = next(
        person for person in missing["known_people"]
        if person["world_id"] == basil.content_id
    )
    assert known["availability"] == "away"
    assert known["condition"]["kind"] == "wounded"
    assert known["last_seen"]["note"] == "They were gone when you returned."
    assert known["unread"] is True

    basil.room_id = room.id
    basil.hp = 0
    basil.is_alive = False
    await _set_world_minute(session, 13)
    dead = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    known = next(
        person for person in dead["known_people"]
        if person["world_id"] == basil.content_id
    )
    assert known["availability"] == "dead"
    assert known["condition"]["kind"] == "dead"

    retained = await world_sync(
        session,
        player_id=player.id,
        current_room_id=distant_room.id,
    )
    known = next(
        person for person in retained["known_people"]
        if person["world_id"] == basil.content_id
    )
    assert known["availability"] == "dead"
    assert known["condition"]["kind"] == "dead"


async def test_expired_room_evidence_cannot_be_discovered(session):
    room, player, _basil = await _player_and_basil(session)
    session.add_all([
        WorldEvent(
            kind="evidence_left",
            world_minute=90,
            room_id=room.id,
            summary="Rain has erased the old chalk tally.",
            visibility="discoverable",
            payload={"expires_at_minute": 100},
        ),
        WorldEvent(
            kind="evidence_left",
            world_minute=99,
            room_id=room.id,
            summary="A fresh chalk tally marks the sluice wall.",
            visibility="discoverable",
            payload={"expires_at_minute": 101},
        ),
    ])
    await _set_world_minute(session, 100)

    payload = await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )
    bodies = {entry["body"] for entry in payload["chronicle"]}
    assert "A fresh chalk tally marks the sluice wall." in bodies
    assert "Rain has erased the old chalk tally." not in bodies
