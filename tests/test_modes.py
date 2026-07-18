"""RoomMode seam tests (Milestone 3, re-grounded by Milestone 7): exploration
resolves immediately, combat still buffers into rounds, and both share one
rules engine.

Since M7 there is no mode knob anywhere — a room is combat because a living
hostile is present (`enemies=` on the fixture), exploration because none is.
The derivation test at the bottom goes through the real loader against seeded
rooms; escalation/de-escalation transitions live in tests/test_escalation.py.
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
    engine = RoomEngine(make_template())  # nobody hostile -> exploration
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
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")

    _, first = submit_move(engine, hero.id, [0, 1])
    _, second = submit_move(engine, hero.id, [0, 1])

    assert first and second
    assert (hero.position.x, hero.position.y) == (1, 3)


def test_combat_move_still_waits_for_all(make_template):
    engine = RoomEngine(make_template(enemies=((3, 3),)))  # a hostile -> combat
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
    engine = RoomEngine(make_template(spawn_points=[(1, 1)]))
    hero, _ = engine.join("Hero")

    events, resolved = submit_move(engine, hero.id, [0, -1])  # into the border wall

    assert not resolved
    assert event_types(events) == [EventType.INVALID_ACTION]
    assert (hero.position.x, hero.position.y) == (1, 1)


def test_exploration_rejects_combat_actions(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")

    for action_data in (
        {"action_type": "attack", "target_id": "enemy_1"},
        {"action_type": "wait"},
    ):
        events, resolved = engine.submit_action(hero.id, action_data)
        assert not resolved
        assert event_types(events) == [EventType.INVALID_ACTION]


def test_exploration_move_onto_door_emits_traversal_event(make_template):
    engine = RoomEngine(make_template(spawn_points=[(2, 1)], connections={(2, 0): 99}))
    hero, _ = engine.join("Hero")

    events, resolved = submit_move(engine, hero.id, [0, -1])

    assert resolved
    door = next(e for e in events if e.event_type is EventType.PLAYER_ENTERED_DOOR)
    assert door.data["to_room_id"] == 99


# --- mode surfaces to the client -------------------------------------------


def test_state_reports_live_derived_mode(make_template):
    assert RoomEngine(make_template()).get_state()["room"]["mode"] == "exploration"
    assert RoomEngine(make_template(enemies=((3, 3),))).get_state()["room"]["mode"] == "combat"


# --- mode derivation over real loaded rooms ----------------------------------


async def test_loaded_rooms_derive_mode_from_hostiles(session):
    hall = await seed_default_rooms(session)
    ante = (await session.execute(
        select(Room).where(Room.name == "The Antechamber"))).scalars().one()

    # The hall seeds hostiles -> combat; the empty antechamber -> exploration.
    assert RoomEngine(await load_room(session, hall.id)).mode_name == "combat"
    assert RoomEngine(await load_room(session, ante.id)).mode_name == "exploration"
