from dataclasses import dataclass
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import backend.main as main
from backend.carriage_store import activate_carriage_stop
from backend.db import Base
from backend.entities import Disposition, NPC, Position
from backend.living_world import store as living_store
from backend.models import (
    CarriageStop,
    NPCRow,
    PlayerRow,
    Room,
    RoomConnection,
    WorldState,
)
from backend.player_store import make_live_player
from backend.procgen.frontier_store import ensure_world_state
from backend.room_engine import RoomEngine
from backend.room_loader import RoomObject
from backend.seeds import get_or_seed_default_room


TUESDAY_0800 = 1440 + 480


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


@dataclass
class _CarriageRuntime:
    maker: async_sessionmaker
    origin: main.RoomRuntime
    player_id: str
    destination_room_id: int
    destination_stop_id: int
    websocket: _FakeWebSocket


def _hidden_by_object_sprite(room_object, point: tuple[int, int]) -> bool:
    cells = room_object.occupied_cells()
    min_x = min(x for x, _ in cells)
    max_x = max(x for x, _ in cells)
    max_y = max(y for _, y in cells)
    logical_width = max_x - min_x + 1
    visual_width, visual_height = room_object.visual_size
    left = min_x + (logical_width - visual_width) / 2
    right = left + visual_width
    top = max_y + 1 - visual_height
    x, y = point
    return left < x + 0.5 < right and top < y + 0.8 and y <= max_y


def test_authored_landing_never_falls_back_to_an_unrelated_room_spawn(
    make_template,
):
    origin = main.RoomRuntime(
        room_id=1,
        engine=RoomEngine(make_template(spawn_points=[(1, 1)])),
    )
    player, _events = origin.engine.join("Traveller")
    coach = RoomObject(
        id="test_coach",
        type="covered_carriage",
        position=(4, 3),
        label="Test Coach",
        description="A physical arrival landmark.",
        footprint=((0, 0), (1, 0), (0, 1), (1, 1)),
        visual_size=(4, 3),
    )
    destination = main.RoomRuntime(
        room_id=2,
        engine=RoomEngine(make_template(
            width=9,
            height=9,
            spawn_points=[(1, 1)],
            capacity=4,
            objects=(coach,),
        )),
    )
    destination_room = destination.engine.room
    for y in range(destination_room.template.height):
        for x in range(destination_room.template.width):
            if (
                destination_room.is_valid_position(x, y)
                and destination_room.is_occupied(x, y) is None
                and coach.distance_from(x, y) <= 2
            ):
                destination_room.add_enemy(
                    "Waiting Passenger",
                    Position(x, y),
                    hp=1,
                    attack_damage=0,
                    defense=0,
                )

    assert destination_room.free_spawn() == (1, 1)
    assert main._transfer_plan(
        origin,
        destination,
        player.id,
        arrival_object_id=coach.id,
    ) is None
    legacy_plan = main._transfer_plan(origin, destination, player.id)
    assert legacy_plan is not None
    assert legacy_plan.player_spawn == (1, 1)


@pytest_asyncio.fixture
async def carriage_runtime(monkeypatch):
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
        rooms = {
            room.content_id: room
            for room in (await session.execute(
                select(Room).where(Room.content_id.in_((
                    "drazna_lantern_quays",
                    "drazna_high_crown",
                )))
            )).scalars()
        }
        quays = rooms["drazna_lantern_quays"]
        crown = rooms["drazna_high_crown"]
        await activate_carriage_stop(session, room_id=quays.id)
        world = await ensure_world_state(session)
        world.world_minute = TUESDAY_0800
        crown_stop = (await session.execute(
            select(CarriageStop).where(CarriageStop.room_id == crown.id)
        )).scalar_one()
        player_row = PlayerRow(
            id="player_carriage_runtime",
            username="carriage-runtime",
            password_hash="unused",
            room_id=quays.id,
            x=9,
            y=1,
            hp=100,
            coins=10,
        )
        session.add(player_row)
        await session.commit()

    monkeypatch.setattr(main, "SessionMaker", maker)
    main.active_rooms.clear()
    main.player_room.clear()

    async def _quiet_sync(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "_send_world_sync_safely", _quiet_sync)
    monkeypatch.setattr(main, "_send_forced_world_advance_safely", _quiet_sync)

    origin = await main.get_or_load_room(quays.id)
    player = make_live_player(player_row)
    origin.engine.attach_player(player, Position(9, 1))
    websocket = _FakeWebSocket()
    origin.connections[player.id] = websocket
    main.player_room[player.id] = quays.id

    yield _CarriageRuntime(
        maker=maker,
        origin=origin,
        player_id=player.id,
        destination_room_id=crown.id,
        destination_stop_id=crown_stop.id,
        websocket=websocket,
    )

    main.active_rooms.clear()
    main.player_room.clear()
    await engine.dispose()


async def test_carriage_arrival_persists_room_fare_and_committed_clock(
    carriage_runtime,
    monkeypatch,
):
    runtime = carriage_runtime

    async def _broken_trigger_consumer(*_args, **_kwargs):
        raise RuntimeError("transient trigger failure")

    # Once the world clock commits, a lagging trigger consumer must not invite
    # a second paid journey over the same time interval.
    monkeypatch.setattr(
        main,
        "advance_authored_triggers",
        _broken_trigger_consumer,
    )
    # The arrival room is deliberately cold: the journey must reload it from
    # persistence and still recover the authored physical landing landmark.
    assert runtime.destination_room_id not in main.active_rooms

    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        {
            "object_id": "drazna_quay_carriage",
            "stop_id": runtime.destination_stop_id,
        },
    )

    assert main.player_room[runtime.player_id] == runtime.destination_room_id
    destination = main.active_rooms[runtime.destination_room_id]
    player = destination.engine.room.get_player(runtime.player_id)
    assert player is not None
    assert player.coins == 8
    landing = destination.engine.room.get_object("drazna_crown_mudwheel")
    assert landing is not None
    assert landing.distance_from(player.position.x, player.position.y) == 1
    message_types = [message["type"] for message in runtime.websocket.sent]
    assert "travel_started" in message_types
    assert "room_changed" in message_types
    assert "carriage_arrived" in message_types
    assert message_types.index("travel_started") < message_types.index(
        "room_changed"
    )

    async with runtime.maker() as session:
        row = await session.get(PlayerRow, runtime.player_id)
        world = await session.get(WorldState, 1)
        assert row.room_id == runtime.destination_room_id
        assert row.coins == 8
        assert world.world_minute == TUESDAY_0800 + 45


async def test_cold_destination_stays_dormant_and_reloads_after_journey(
    carriage_runtime,
    monkeypatch,
):
    runtime = carriage_runtime
    async with runtime.maker() as session:
        traveller = (await session.execute(
            select(NPCRow).where(NPCRow.content_id == "nera-bell")
        )).scalar_one()
        original_room_id = traveller.room_id
        original_position = (traveller.x, traveller.y)
        traveller.room_id = runtime.destination_room_id
        traveller.x = 7
        traveller.y = 7
        await session.commit()
        traveller_db_id = traveller.id

    async def _advance_dormant_world(
        session,
        *,
        wall_now,
        active_room_ids,
        forced_minutes,
    ):
        del wall_now
        # The destination existed in memory for preflight, but that snapshot
        # must not make it live authority for the duration of the ride.
        assert runtime.destination_room_id not in active_room_ids
        traveller = await session.get(NPCRow, traveller_db_id)
        traveller.room_id = original_room_id
        traveller.x, traveller.y = original_position
        world = await session.get(WorldState, 1)
        from_minute = world.world_minute
        world.world_minute += forced_minutes
        await session.commit()
        return SimpleNamespace(
            from_minute=from_minute,
            to_minute=world.world_minute,
        )

    async def _quiet_triggers(
        _session,
        *,
        from_minute,
        to_minute,
        active_room_ids,
    ):
        assert from_minute == TUESDAY_0800
        assert to_minute == TUESDAY_0800 + 45
        assert runtime.destination_room_id not in active_room_ids

    monkeypatch.setattr(main, "advance_living_world", _advance_dormant_world)
    monkeypatch.setattr(main, "advance_authored_triggers", _quiet_triggers)

    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        {
            "object_id": "drazna_quay_carriage",
            "stop_id": runtime.destination_stop_id,
        },
    )

    destination = main.active_rooms[runtime.destination_room_id]
    assert all(
        npc.db_id != traveller_db_id
        for npc in destination.engine.room.npcs.values()
    )
    async with runtime.maker() as session:
        traveller = await session.get(NPCRow, traveller_db_id)
        assert traveller.room_id == original_room_id
        assert (traveller.x, traveller.y) == original_position


async def test_cold_preflight_survives_multiple_dormant_arrivals(
    carriage_runtime,
    monkeypatch,
):
    runtime = carriage_runtime
    # A compact room makes the failure adversarial: seven legal visible
    # carriage-apron cells are enough for a four-person party, but the old
    # dormant selector consumed four of them in its first seven arrivals.
    async with runtime.maker() as session:
        destination = await session.get(Room, runtime.destination_room_id)
        destination.width = 6
        destination.height = 6
        destination.terrain = [
            "######",
            "#....#",
            "#....#",
            "#....#",
            "#....#",
            "######",
        ]
        destination.objects = [{
            "id": "drazna_crown_mudwheel",
            "type": "drazna_mudwheel_stop",
            "x": 1,
            "y": 1,
        }]
        destination.spawn_points = [[1, 4], [2, 4], [3, 4], [4, 4]]
        destination.enemy_spawns = []
        await session.execute(delete(RoomConnection).where(
            RoomConnection.from_room_id == runtime.destination_room_id,
        ))
        travellers = (await session.execute(
            select(NPCRow).where(
                NPCRow.is_alive.is_(True),
                NPCRow.room_id.not_in({
                    runtime.origin.room_id,
                    runtime.destination_room_id,
                }),
            ).order_by(NPCRow.id).limit(7)
        )).scalars().all()
        assert len(travellers) == 7
        traveller_ids = [traveller.id for traveller in travellers]
        await session.commit()

    origin_room = runtime.origin.engine.room
    origin_exit_tiles = {
        *origin_room.template.connections.keys(),
        *origin_room.template.frontier_exits.keys(),
    }
    free_origin_positions = [
        (x, y)
        for y in range(origin_room.template.height)
        for x in range(origin_room.template.width)
        if origin_room.is_valid_position(x, y)
        and origin_room.is_occupied(x, y) is None
        and (x, y) not in origin_exit_tiles
    ]
    followers = []
    for index, position in enumerate(free_origin_positions[:3]):
        follower = NPC(
            id=f"npc_reserved_apron_follower_{index}",
            db_id=91_000 + index,
            name=f"Apron Follower {index}",
            position=Position(*position),
            hp=20,
            max_hp=20,
            defense=1,
            attack_damage=2,
            disposition=Disposition.FRIENDLY,
            party_owner_id=runtime.player_id,
        )
        origin_room.add_npc(follower)
        followers.append(follower)

    advance_calls = 0
    dormant_arrivals: list[tuple[int, int]] = []

    async def _advance_with_arrivals(
        session,
        *,
        wall_now,
        active_room_ids,
        forced_minutes,
    ):
        nonlocal advance_calls
        del wall_now
        advance_calls += 1
        assert runtime.destination_room_id not in active_room_ids
        for traveller_id in traveller_ids:
            traveller = await session.get(NPCRow, traveller_id)
            position = await living_store.arrival_position(
                session,
                room_id=runtime.destination_room_id,
                from_room_id=traveller.room_id,
            )
            if position is None:
                continue
            traveller.room_id = runtime.destination_room_id
            traveller.x, traveller.y = position
            dormant_arrivals.append(position)
        world = await session.get(WorldState, 1)
        from_minute = world.world_minute
        world.world_minute += forced_minutes
        await session.commit()
        return SimpleNamespace(
            from_minute=from_minute,
            to_minute=world.world_minute,
        )

    async def _quiet_triggers(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "advance_living_world", _advance_with_arrivals)
    monkeypatch.setattr(main, "advance_authored_triggers", _quiet_triggers)

    request = {
        "object_id": "drazna_quay_carriage",
        "stop_id": runtime.destination_stop_id,
    }
    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        request,
    )

    assert len(dormant_arrivals) == 3
    carriage_cells = ((1, 1), (2, 1), (1, 2), (2, 2))
    assert all(
        min(
            abs(x - carriage_x) + abs(y - carriage_y)
            for carriage_x, carriage_y in carriage_cells
        ) > 2
        for x, y in dormant_arrivals
    )
    assert main.player_room[runtime.player_id] == runtime.destination_room_id
    destination_runtime = main.active_rooms[runtime.destination_room_id]
    assert destination_runtime.engine.room.get_player(runtime.player_id)
    assert all(
        follower.id in destination_runtime.engine.room.npcs
        for follower in followers
    )
    assert advance_calls == 1

    # A duplicate client request after the successful arrival cannot advance
    # the clock again: the player is no longer beside the origin carriage.
    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        request,
    )
    assert advance_calls == 1
    assert sum(
        message["type"] == "travel_started"
        for message in runtime.websocket.sent
    ) == 1
    async with runtime.maker() as session:
        world = await session.get(WorldState, 1)
        assert world.world_minute == TUESDAY_0800 + 45


async def test_carriage_lands_a_four_person_party_beside_the_mudwheel(
    carriage_runtime,
):
    runtime = carriage_runtime
    origin_room = runtime.origin.engine.room
    exit_tiles = {
        *origin_room.template.connections.keys(),
        *origin_room.template.frontier_exits.keys(),
    }
    free_origin_positions = [
        (x, y)
        for y in range(origin_room.template.height)
        for x in range(origin_room.template.width)
        if origin_room.is_valid_position(x, y)
        and origin_room.is_occupied(x, y) is None
        and (x, y) not in exit_tiles
    ]
    followers = []
    for index, position in enumerate(free_origin_positions[:3]):
        follower = NPC(
            id=f"npc_carriage_follower_{index}",
            db_id=90_000 + index,
            name=f"Follower {index}",
            position=Position(*position),
            hp=20,
            max_hp=20,
            defense=1,
            attack_damage=2,
            disposition=Disposition.FRIENDLY,
            party_owner_id=runtime.player_id,
        )
        origin_room.add_npc(follower)
        followers.append(follower)

    assert runtime.destination_room_id not in main.active_rooms
    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        {
            "object_id": "drazna_quay_carriage",
            "stop_id": runtime.destination_stop_id,
        },
    )

    destination = main.active_rooms[runtime.destination_room_id]
    room = destination.engine.room
    landing = room.get_object("drazna_crown_mudwheel")
    assert landing is not None
    party = [
        room.get_player(runtime.player_id),
        *(room.npcs[follower.id] for follower in followers),
    ]
    positions = {
        (actor.position.x, actor.position.y)
        for actor in party
        if actor is not None
    }
    assert len(party) == len(positions) == 4
    destination_exit_tiles = {
        *room.template.connections.keys(),
        *room.template.frontier_exits.keys(),
    }
    for position in positions:
        assert room.is_valid_position(*position)
        assert position not in destination_exit_tiles
        assert landing.distance_from(*position) <= 2
        assert not _hidden_by_object_sprite(landing, position)
    assert all(follower.id not in origin_room.npcs for follower in followers)


async def test_grey_heron_round_trip_uses_both_physical_coaches(
    carriage_runtime,
):
    runtime = carriage_runtime
    sunday_0500 = 6 * 1440 + 300
    next_wednesday_0530 = 9 * 1440 + 330
    async with runtime.maker() as session:
        oakrun = (await session.execute(
            select(Room).where(Room.content_id == "oakrun_crossroads")
        )).scalar_one()
        oakrun_stop = (await session.execute(
            select(CarriageStop).where(CarriageStop.room_id == oakrun.id)
        )).scalar_one()
        quays_stop = (await session.execute(
            select(CarriageStop).where(
                CarriageStop.room_id == runtime.origin.room_id
            )
        )).scalar_one()
        player_row = await session.get(PlayerRow, runtime.player_id)
        world = await session.get(WorldState, 1)
        player_row.coins = 60
        world.world_minute = sunday_0500
        await session.commit()
    runtime.origin.engine.room.get_player(runtime.player_id).coins = 60

    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        {
            "object_id": "drazna_quay_carriage",
            "stop_id": oakrun_stop.id,
        },
    )
    oakrun_runtime = main.active_rooms[oakrun.id]
    player = oakrun_runtime.engine.room.get_player(runtime.player_id)
    oakrun_carriage = oakrun_runtime.engine.room.get_object(
        "oakrun_covered_carriage"
    )
    assert player is not None
    assert oakrun_carriage is not None
    assert oakrun_carriage.distance_from(
        player.position.x,
        player.position.y,
    ) == 1

    async with runtime.maker() as session:
        world = await session.get(WorldState, 1)
        world.world_minute = next_wednesday_0530
        await session.commit()
    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        {
            "object_id": "oakrun_covered_carriage",
            "stop_id": quays_stop.id,
        },
    )
    quays_runtime = main.active_rooms[runtime.origin.room_id]
    player = quays_runtime.engine.room.get_player(runtime.player_id)
    quays_carriage = quays_runtime.engine.room.get_object(
        "drazna_quay_carriage"
    )
    assert player is not None
    assert quays_carriage is not None
    assert quays_carriage.distance_from(
        player.position.x,
        player.position.y,
    ) == 1


async def test_full_live_destination_rejects_before_world_time_or_fare_changes(
    carriage_runtime,
):
    runtime = carriage_runtime
    destination = await main.get_or_load_room(runtime.destination_room_id)
    for index in range(destination.engine.room.template.capacity):
        destination.engine.join(f"Resident {index}")

    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        {
            "object_id": "drazna_quay_carriage",
            "stop_id": runtime.destination_stop_id,
        },
    )

    assert main.player_room[runtime.player_id] == runtime.origin.room_id
    assert all(
        message["type"] != "travel_started"
        for message in runtime.websocket.sent
    )
    assert runtime.websocket.sent[-1] == {
        "type": "error",
        "message": "There is no room for your party at that stop.",
    }
    async with runtime.maker() as session:
        row = await session.get(PlayerRow, runtime.player_id)
        world = await session.get(WorldState, 1)
        assert row.room_id == runtime.origin.room_id
        assert row.coins == 10
        assert world.world_minute == TUESDAY_0800


async def test_cold_blocked_landing_rejects_before_world_time_or_fare_changes(
    carriage_runtime,
):
    runtime = carriage_runtime
    # Fill every visible, walkable cell in the two-tile carriage apron with
    # seeded enemies while leaving the room's unrelated south spawn open.
    async with runtime.maker() as session:
        destination_row = await session.get(Room, runtime.destination_room_id)
        destination_row.enemy_spawns = [
            {"enemy_id": 14, "x": x, "y": y}
            for x, y in (
                (4, 1),
                (1, 3),
                (2, 3),
                (3, 3),
                (1, 4),
                (2, 4),
            )
        ]
        await session.commit()
    assert runtime.destination_room_id not in main.active_rooms

    await main.handle_carriage_travel(
        runtime.websocket,
        runtime.player_id,
        {
            "object_id": "drazna_quay_carriage",
            "stop_id": runtime.destination_stop_id,
        },
    )

    assert main.player_room[runtime.player_id] == runtime.origin.room_id
    assert runtime.destination_room_id not in main.active_rooms
    assert all(
        message["type"] != "travel_started"
        for message in runtime.websocket.sent
    )
    assert runtime.websocket.sent[-1] == {
        "type": "error",
        "message": "There is no room for your party at that stop.",
    }
    player = runtime.origin.engine.room.get_player(runtime.player_id)
    assert player is not None
    assert player.coins == 10
    async with runtime.maker() as session:
        row = await session.get(PlayerRow, runtime.player_id)
        world = await session.get(WorldState, 1)
        assert row.room_id == runtime.origin.room_id
        assert row.coins == 10
        assert world.world_minute == TUESDAY_0800
