from dataclasses import dataclass

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import backend.main as main
from backend.carriage_store import activate_carriage_stop
from backend.db import Base
from backend.entities import Position
from backend.models import CarriageStop, PlayerRow, Room, WorldState
from backend.player_store import make_live_player
from backend.procgen.frontier_store import ensure_world_state
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
