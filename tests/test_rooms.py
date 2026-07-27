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
from backend.entities import Disposition, Position
from backend.events import EventType, GameEvent
from backend.seeds import seed_default_rooms
from backend.models import NPCRow, Room, WorldEvent, WorldFact


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

    await main.maybe_evict(runtime)  # no players -> evicts

    assert hall.id not in main.active_rooms
    assert runtime.timeout_task is None
    await asyncio.sleep(0)  # let cancellation land
    assert task.cancelled()


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)


async def test_delayed_world_sync_cannot_observe_a_room_after_transfer(
    world_db,
    monkeypatch,
):
    hall, ante = world_db
    origin, player = await join_room(hall.id, "Reader")
    await main.get_or_load_room(ante.id)
    websocket = _FakeWS()
    origin.connections[player.id] = websocket
    calls = []

    async def fake_world_sync(
        session,
        *,
        player_id,
        current_room_id,
        commit,
    ):
        calls.append((player_id, current_room_id, commit))
        return {"type": "world_sync"}

    monkeypatch.setattr(main, "world_sync", fake_world_sync)
    # A task captured the origin, then ran only after traversal had changed
    # the authoritative registry.
    main.player_room[player.id] = ante.id
    await main._send_world_sync_safely(origin, player.id, hall.id)

    assert calls == []
    assert websocket.sent == []


async def test_world_sync_rolls_back_if_player_moves_during_database_work(
    world_db,
    monkeypatch,
):
    hall, ante = world_db
    origin, player = await join_room(hall.id, "Reader")
    await main.get_or_load_room(ante.id)
    websocket = _FakeWS()
    origin.connections[player.id] = websocket

    async def move_during_sync(
        session,
        *,
        player_id,
        current_room_id,
        commit,
    ):
        session.add(WorldEvent(
            dedupe_key="stale-observation",
            kind="evidence_left",
            world_minute=1,
            room_id=current_room_id,
            summary="This observation must be rolled back.",
            visibility="discoverable",
        ))
        await session.flush()
        main._bind_player_room(player_id, ante.id)
        return {"type": "world_sync"}

    monkeypatch.setattr(main, "world_sync", move_during_sync)
    await main._send_world_sync_safely(origin, player.id, hall.id)

    assert websocket.sent == []
    async with main.SessionMaker() as session:
        stale = (await session.execute(
            select(WorldEvent).where(
                WorldEvent.dedupe_key == "stale-observation"
            )
        )).scalar_one_or_none()
        assert stale is None


async def test_situation_outcome_reconciles_a_domain_disposition_and_saves(
    world_db,
):
    hall, _ante = world_db
    runtime, _player = await join_room(hall.id, "Warden")
    actor = next(
        npc
        for npc in runtime.engine.room.npcs.values()
        if npc.persona.get("id") == "mara-pillared-hall"
    )
    actor.disposition = Disposition.HOSTILE

    _events, changed = main._reconcile_situation_actor(
        runtime,
        "mara-pillared-hall",
        "neutral",
    )
    assert changed is True
    assert actor.disposition is Disposition.NEUTRAL
    # Serialization and persistence both require the enum, not a raw string.
    runtime.engine.get_state()
    await main._save_individuals(runtime)
    async with main.SessionMaker() as session:
        saved = await session.get(NPCRow, actor.db_id)
        assert saved.disposition == "neutral"


async def test_dev_reset_discards_live_rooms_and_restores_seeds(world_db):
    hall, _ante = world_db
    runtime, player = await join_room(hall.id, "Hero")
    ws = _FakeWS()
    runtime.connections[player.id] = ws

    # Kill the hall's recruitable NPC — the played-in state we want gone.
    mara = next(n for n in runtime.engine.room.npcs.values() if n.name == "Mara")
    mara.is_alive = False

    await main.handle_dev_reset(ws)

    # Live registries cleared and the client told to reload.
    assert main.active_rooms == {}
    assert main.player_room == {}
    assert any(m["type"] == "world_reset" for m in ws.sent)

    # Reloading the hall pulls a FRESH, living Mara from the reseeded rows.
    reloaded = await main.get_or_load_room(hall.id)
    mara2 = next(n for n in reloaded.engine.room.npcs.values() if n.name == "Mara")
    assert mara2.is_alive


async def test_active_npc_death_writes_through_before_room_eviction(world_db):
    hall, _ = world_db
    runtime, player = await join_room(hall.id, "Witness")
    mara = next(n for n in runtime.engine.room.npcs.values() if n.name == "Mara")
    mara.party_owner_id = "player_old"
    mara.hp = 0
    mara.is_alive = False
    death = GameEvent(
        EventType.NPC_DIED,
        {"target_id": mara.id, "killer_id": "player_killer"},
        runtime.engine.room.round,
    )

    await main.handle_round_events(runtime, [death])
    # Retrying the same round edge remains idempotent.
    await main.handle_round_events(runtime, [death])

    async with main.SessionMaker() as session:
        row = await session.get(NPCRow, mara.db_id)
        assert (row.hp, row.is_alive, row.party_owner_id) == (0, False, None)
        deaths = (await session.execute(
            select(WorldEvent).where(
                WorldEvent.kind == "npc_died",
                WorldEvent.target_id == "mara-pillared-hall",
            )
        )).scalars().all()
        assert len(deaths) == 1
        assert deaths[0].visibility == "witnessed"
        assert deaths[0].witnesses == [player.id]
        fate = (await session.execute(
            select(WorldFact).where(
                WorldFact.fact_key == "npc-fate:mara-pillared-hall"
            )
        )).scalar_one()
        assert fate.value["is_alive"] is False
        assert fate.source_event_id == deaths[0].id


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
    assert (arrived.position.x, arrived.position.y) == (1, 2)  # beside return door

    # Step back through the west door -> a FRESH hall (rooms have no memory).
    await step_through_door(ante_runtime, player.id, [-1, 0])

    assert main.player_room[player.id] == hall.id
    assert ante.id not in main.active_rooms
    back = main.active_rooms[hall.id].engine.room.get_player(player.id)
    assert back is player and back.hp == 5
    # The hall has two links back to the antechamber. The stable northern
    # reverse edge wins, instead of the unrelated first capacity spawn.
    assert (back.position.x, back.position.y) == (4, 1)


async def test_owned_follower_travels_and_persists_with_player(world_db):
    hall, ante = world_db
    runtime, player = await join_room(hall.id, "Hero")
    mara = next(npc for npc in runtime.engine.room.npcs.values() if npc.name == "Mara")
    mara.party_owner_id = player.id

    await main._transfer_player(runtime, player.id, ante.id)

    destination = main.active_rooms[ante.id]
    assert mara.id not in runtime.engine.room.npcs
    assert destination.engine.room.npcs[mara.id] is mara
    assert abs(mara.position.x - player.position.x) + abs(
        mara.position.y - player.position.y
    ) == 1
    async with main.SessionMaker() as session:
        saved = await session.get(NPCRow, mara.db_id)
        assert saved.room_id == ante.id
        assert (saved.x, saved.y) == (mara.position.x, mara.position.y)


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
    await main.maybe_evict(runtime)
    assert hall.id not in main.active_rooms

    fresh = await main.get_or_load_room(hall.id)
    fresh_goblin = next(e for e in fresh.engine.room.enemies.values() if e.name == "Goblin")
    assert fresh is not runtime
    assert fresh_goblin.hp == 6  # rooms have no memory — reseeded from the DB
