"""RoomMode seam tests (Milestone 3): exploration resolves immediately,
combat still buffers into rounds, and both share one rules engine.

Pure-engine tests use the synthetic `make_template` fixture; the mode-inference
test at the bottom goes through the real loader against seeded rooms.
"""
from sqlalchemy import select

from backend.events import EventType
from backend.room_engine import RoomEngine
from backend.room_loader import load_room
from backend.seeds import seed_default_rooms
from backend.models import Room


def event_types(events):
    return [e.event_type for e in events]


def submit_move(engine, player_id, direction):
    return engine.submit_action(player_id, {"action_type": "move", "direction": direction})


# --- exploration timing ---------------------------------------------------


def test_exploration_move_resolves_immediately(make_template):
    engine = RoomEngine(make_template(mode="exploration"))
    hero, _ = engine.join("Hero")
    engine.join("Bystander")  # never acts — must not block the hero

    events, resolved = submit_move(engine, hero.id, [0, 1])

    assert resolved
    assert EventType.PLAYER_MOVED in event_types(events)
    assert (hero.position.x, hero.position.y) == (1, 2)
    # Nothing buffered, no round machinery ran.
    assert engine.room.pending_actions == {}
    assert engine.room.round == 0
    assert EventType.ROUND_STARTED not in event_types(events)


def test_exploration_allows_consecutive_moves(make_template):
    # No "one action per round" limit — the same player can keep walking.
    engine = RoomEngine(make_template(mode="exploration"))
    hero, _ = engine.join("Hero")

    _, first = submit_move(engine, hero.id, [0, 1])
    _, second = submit_move(engine, hero.id, [0, 1])

    assert first and second
    assert (hero.position.x, hero.position.y) == (1, 3)


def test_combat_move_still_waits_for_all(make_template):
    engine = RoomEngine(make_template(mode="combat"))
    hero, _ = engine.join("Hero")
    engine.join("Slowpoke")

    events, resolved = submit_move(engine, hero.id, [0, 1])

    assert not resolved
    assert events == []
    # Buffered, not applied — the hero has not moved yet.
    assert hero.id in engine.room.pending_actions
    assert (hero.position.x, hero.position.y) == (1, 1)


# --- shared rules engine ---------------------------------------------------


def test_exploration_shares_movement_validation(make_template):
    engine = RoomEngine(make_template(spawn_points=[(1, 1)], mode="exploration"))
    hero, _ = engine.join("Hero")

    events, resolved = submit_move(engine, hero.id, [0, -1])  # into the border wall

    assert not resolved
    assert event_types(events) == [EventType.INVALID_ACTION]
    assert (hero.position.x, hero.position.y) == (1, 1)


def test_exploration_rejects_combat_actions(make_template):
    engine = RoomEngine(make_template(mode="exploration"))
    hero, _ = engine.join("Hero")

    for action_data in (
        {"action_type": "attack", "target_id": "enemy_1"},
        {"action_type": "bomb", "target_tile": [2, 2]},
        {"action_type": "wait"},
    ):
        events, resolved = engine.submit_action(hero.id, action_data)
        assert not resolved
        assert event_types(events) == [EventType.INVALID_ACTION]


def test_exploration_move_onto_door_emits_traversal_event(make_template):
    engine = RoomEngine(make_template(spawn_points=[(2, 1)], connections={(2, 0): 99}, mode="exploration"))
    hero, _ = engine.join("Hero")

    events, resolved = submit_move(engine, hero.id, [0, -1])

    assert resolved
    door = next(e for e in events if e.event_type is EventType.PLAYER_ENTERED_DOOR)
    assert door.data["to_room_id"] == 99


# --- mode surfaces to the client -------------------------------------------


def test_state_reports_room_mode(make_template):
    assert RoomEngine(make_template(mode="exploration")).get_state()["room"]["mode"] == "exploration"
    assert RoomEngine(make_template(mode="combat")).get_state()["room"]["mode"] == "combat"


# --- mode inference at the loader seam --------------------------------------


async def test_load_room_infers_mode_from_enemies(session):
    hall = await seed_default_rooms(session)
    ante = (await session.execute(
        select(Room).where(Room.name == "The Antechamber"))).scalars().one()

    # The hall has enemies -> combat; the empty antechamber -> exploration.
    assert (await load_room(session, hall.id)).mode == "combat"
    assert (await load_room(session, ante.id)).mode == "exploration"
