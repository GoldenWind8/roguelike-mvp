"""Integration tests for the room registry and traversal at the async edge.

These exercise backend.main directly (get_or_load_room, handle_round_events)
against a seeded in-memory DB — no websockets, so runtime.connections stays
empty and the broadcast helpers are harmless no-ops.
"""
import asyncio

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import backend.main as main
from backend.db import Base
from backend.entities import Position
from backend.seeds import seed_default_rooms
from backend.models import Room


@pytest_asyncio.fixture
async def world_db(monkeypatch):
    """Seeded in-memory DB wired into backend.main: SessionMaker is patched,
    registries are cleared, and (hall, ante) Room rows are returned."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with maker() as session:
        hall = await seed_default_rooms(session)
        ante = (await session.execute(
            select(Room).where(Room.name == "The Antechamber"))).scalars().one()

    monkeypatch.setattr(main, "SessionMaker", maker)
    monkeypatch.setattr(main, "default_room_id", hall.id)
    main.active_rooms.clear()
    main.player_room.clear()

    yield hall, ante

    main.active_rooms.clear()
    main.player_room.clear()
    await engine.dispose()


async def join_room(room_id: int, name: str):
    """The join flow minus the websocket: runtime + registry bookkeeping."""
    runtime = await main.get_or_load_room(room_id)
    player, _ = runtime.engine.join(name)
    main.player_room[player.id] = room_id
    return runtime, player


def submit_move(runtime, player_id, direction):
    return runtime.engine.submit_action(
        player_id, {"action_type": "move", "direction": direction})


async def step_through_door(runtime, player_id, direction):
    events, resolved = submit_move(runtime, player_id, direction)
    assert resolved
    await main.handle_round_events(runtime, events)
    return events


async def test_get_or_load_room_caches_runtime(world_db):
    hall, _ = world_db
    first = await main.get_or_load_room(hall.id)
    second = await main.get_or_load_room(hall.id)
    assert first is second
    assert main.active_rooms == {hall.id: first}


async def test_eviction_cancels_timeout(world_db):
    hall, _ = world_db
    runtime = await main.get_or_load_room(hall.id)
    main.start_round_timeout(runtime)
    task = runtime.timeout_task

    main.maybe_evict(runtime)  # no players -> evicts

    assert hall.id not in main.active_rooms
    assert runtime.timeout_task is None
    await asyncio.sleep(0)  # let cancellation land
    assert task.cancelled()


async def test_traversal_round_trip(world_db):
    hall, ante = world_db
    runtime, player = await join_room(hall.id, "Hero")
    player.hp = 5  # battle-worn — must survive both crossings

    # Walk to just below the south door, then step through it.
    runtime.engine.room.move_entity(player.id, Position(4, 8))
    await step_through_door(runtime, player.id, [0, 1])

    assert main.player_room[player.id] == ante.id
    assert hall.id not in main.active_rooms          # sole player left -> evicted
    ante_runtime = main.active_rooms[ante.id]
    arrived = ante_runtime.engine.room.get_player(player.id)
    assert arrived is player and arrived.hp == 5
    assert (arrived.position.x, arrived.position.y) == (1, 2)  # first free spawn

    # Step back through the west door -> a FRESH hall (rooms have no memory).
    await step_through_door(ante_runtime, player.id, [-1, 0])

    assert main.player_room[player.id] == hall.id
    assert ante.id not in main.active_rooms
    back = main.active_rooms[hall.id].engine.room.get_player(player.id)
    assert back is player and back.hp == 5
    assert (back.position.x, back.position.y) == (3, 8)  # hall's first spawn


async def test_traversal_denied_when_destination_full(world_db):
    hall, ante = world_db
    await join_room(ante.id, "Resident1")
    await join_room(ante.id, "Resident2")  # antechamber capacity is 2 -> full

    runtime, player = await join_room(hall.id, "Hero")
    runtime.engine.room.move_entity(player.id, Position(4, 8))
    await step_through_door(runtime, player.id, [0, 1])

    # Denied: still in the hall, standing on the door tile, nothing corrupted.
    assert main.player_room[player.id] == hall.id
    assert runtime.engine.room.get_player(player.id) is player
    assert (player.position.x, player.position.y) == (4, 9)
    assert len(main.active_rooms[ante.id].engine.room.players) == 2


async def test_two_players_one_slot_exactly_one_transfers(world_db):
    hall, ante = world_db
    await join_room(ante.id, "Resident")  # leaves exactly one free slot

    runtime, p1 = await join_room(hall.id, "First")
    _, p2 = await join_room(hall.id, "Second")
    # p1 spawned at (3,8); park them below the south door. p2 spawned at
    # (4,8); park them below the north door.
    runtime.engine.room.move_entity(p2.id, Position(4, 1))
    runtime.engine.room.move_entity(p1.id, Position(4, 8))

    submit_move(runtime, p1.id, [0, 1])                 # onto (4, 9)
    events, resolved = submit_move(runtime, p2.id, [0, -1])  # onto (4, 0)
    assert resolved
    await main.handle_round_events(runtime, events)

    ante_players = main.active_rooms[ante.id].engine.room.players
    transferred = [p for p in (p1, p2) if p.id in ante_players]
    assert len(transferred) == 1                        # capacity enforced
    stayed = p2 if transferred == [p1] else p1
    assert main.player_room[stayed.id] == hall.id
    assert runtime.engine.room.get_player(stayed.id) is stayed


async def test_transfer_to_unloadable_room_is_denied_cleanly(world_db):
    hall, _ = world_db
    runtime, player = await join_room(hall.id, "Hero")

    await main._transfer_player(runtime, player.id, 9999)  # no such room

    assert main.player_room[player.id] == hall.id
    assert runtime.engine.room.get_player(player.id) is player
    assert 9999 not in main.active_rooms


async def test_dead_player_is_not_transferred(world_db):
    hall, ante = world_db
    runtime, player = await join_room(hall.id, "Hero")
    player.is_alive = False  # died on the doorstep (enemy phase runs after moves)

    await main._transfer_player(runtime, player.id, ante.id)

    assert main.player_room[player.id] == hall.id
    assert runtime.engine.room.get_player(player.id) is player
    assert ante.id not in main.active_rooms  # never even loaded


async def test_room_state_resets_after_eviction(world_db):
    hall, _ = world_db
    runtime, player = await join_room(hall.id, "Hero")
    goblin = next(e for e in runtime.engine.room.enemies.values() if e.name == "Goblin")
    goblin.hp = 1

    runtime.engine.remove_player(player.id)
    main.player_room.pop(player.id)
    main.maybe_evict(runtime)
    assert hall.id not in main.active_rooms

    fresh = await main.get_or_load_room(hall.id)
    fresh_goblin = next(e for e in fresh.engine.room.enemies.values() if e.name == "Goblin")
    assert fresh is not runtime
    assert fresh_goblin.hp == 6  # rooms have no memory — reseeded from the DB
