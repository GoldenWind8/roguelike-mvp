from sqlalchemy import select

from backend.entities import Disposition, NPC, Position
from backend.events import EventType
from backend.main import RoomRuntime, _active_npc_steering, _step_active_npcs
from backend.models import NPCGoal, Room
from backend.npc_store import load_npcs
from backend.room_engine import RoomEngine
from backend.room_loader import load_room
from backend.seeds import NORTH_ROAD_ROOM, get_or_seed_default_room


def test_visible_npc_movement_is_programmatic_and_avoids_exits(make_template):
    template = make_template(
        width=8,
        height=8,
        spawn_points=[(1, 1), (2, 1)],
        doors=((7, 4),),
    )
    runtime = RoomRuntime(room_id=1, engine=RoomEngine(template))
    runtime.engine.join("Watcher")
    npc = NPC(
        id="npc_99",
        db_id=99,
        name="Walker",
        position=Position(4, 4),
        hp=20,
        max_hp=20,
        defense=1,
        disposition=Disposition.NEUTRAL,
        persona={"id": "walker", "role": "road keeper"},
    )
    runtime.engine.room.add_npc(npc)

    before = (npc.position.x, npc.position.y)
    events = _step_active_npcs(runtime, 7)
    after = (npc.position.x, npc.position.y)

    assert len(events) == 1
    assert events[0].event_type is EventType.NPC_MOVED
    assert sum(abs(a - b) for a, b in zip(before, after)) == 1
    assert after != (7, 4)
    assert npc.activity["kind"] == "working"


def test_followers_do_not_wander_from_their_party(make_template):
    runtime = RoomRuntime(room_id=1, engine=RoomEngine(make_template(width=8, height=8)))
    npc = NPC(
        id="npc_100",
        db_id=100,
        name="Companion",
        position=Position(4, 4),
        hp=20,
        max_hp=20,
        defense=1,
        disposition=Disposition.FRIENDLY,
        persona={"id": "companion", "role": "scout"},
        party_owner_id="player_owner",
    )
    runtime.engine.room.add_npc(npc)

    assert _step_active_npcs(runtime, 8) == []
    assert (npc.position.x, npc.position.y) == (4, 4)


def test_visible_npc_steps_toward_the_exit_chosen_by_its_intention(make_template):
    template = make_template(
        width=8,
        height=8,
        doors=((7, 4),),
    )
    runtime = RoomRuntime(room_id=1, engine=RoomEngine(template))
    npc = NPC(
        id="npc_101",
        db_id=101,
        name="Wayfarer",
        position=Position(3, 4),
        hp=20,
        max_hp=20,
        defense=1,
        disposition=Disposition.NEUTRAL,
        persona={"id": "wayfarer", "role": "courier"},
    )
    runtime.engine.room.add_npc(npc)

    events = _step_active_npcs(
        runtime,
        9,
        steering={"wayfarer": (7, 4)},
    )

    assert len(events) == 1
    assert (npc.position.x, npc.position.y) == (4, 4)
    assert npc.activity["kind"] == "travelling"


async def test_durable_intention_resolves_to_the_next_local_door(session):
    oakrun = await get_or_seed_default_room(session)
    north_road = (await session.execute(
        select(Room).where(Room.name == NORTH_ROAD_ROOM["name"])
    )).scalar_one()
    template = await load_room(session, oakrun.id)
    runtime = RoomRuntime(room_id=oakrun.id, engine=RoomEngine(template))
    for npc in await load_npcs(session, oakrun.id):
        runtime.engine.room.add_npc(npc)
    session.add(NPCGoal(
        npc_content_id="basil-oakrun",
        goal_key="test-road-intention",
        kind="travel",
        priority=99,
        status="active",
        created_at_minute=0,
        next_deliberation_minute=500,
        context={
            "current_intention": {
                "kind": "pursue_goal",
                "target_room_id": north_road.id,
            },
        },
    ))
    await session.commit()

    steering = await _active_npc_steering(session, runtime)

    expected_exit = next(
        tile for tile, room_id in template.connections.items()
        if room_id == north_road.id
    )
    assert steering["basil-oakrun"] == expected_exit
    # Return the read transaction before RoomEngine/NPC objects leave scope;
    # this keeps the shared async fixture from relying on GC timing.
    await session.rollback()
    await session.close()
