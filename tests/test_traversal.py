"""Pure-engine traversal tests: door events and attach/detach.

No DB here — worlds are built from the synthetic `make_template` fixture. The
async edge (socket rewiring, room loading) is covered in test_rooms.py.
"""
from backend.entities import Disposition, NPC, Position
from backend.events import EventType
from backend.room_engine import RoomEngine
from backend.room_state import RoomState

import pytest

DEST_ROOM_ID = 99


def event_types(events):
    return [e.event_type for e in events]


def submit_move(engine, player_id, direction):
    return engine.submit_action(player_id, {"action_type": "move", "direction": direction})


def test_move_onto_connected_door_emits_event(make_template):
    # Door in the top border at (2, 0), spawn directly below it.
    engine = RoomEngine(make_template(spawn_points=[(2, 1)], connections={(2, 0): DEST_ROOM_ID}))
    player, _ = engine.join("Hero")

    events, resolved = submit_move(engine, player.id, [0, -1])

    assert resolved
    door_events = [e for e in events if e.event_type is EventType.PLAYER_ENTERED_DOOR]
    assert len(door_events) == 1
    assert door_events[0].data["player_id"] == player.id
    assert door_events[0].data["to_room_id"] == DEST_ROOM_ID
    assert door_events[0].data["position"] == [2, 0]
    # The move itself is announced before the door intent.
    assert events.index(next(e for e in events if e.event_type is EventType.PLAYER_MOVED)) \
        < events.index(door_events[0])


def test_move_onto_door_without_connection_is_plain_move(make_template):
    engine = RoomEngine(make_template(spawn_points=[(2, 1)], doors=((2, 0),)))
    player, _ = engine.join("Hero")

    events, _ = submit_move(engine, player.id, [0, -1])

    assert EventType.PLAYER_MOVED in event_types(events)
    assert EventType.PLAYER_ENTERED_DOOR not in event_types(events)


def test_waiting_on_door_emits_no_event(make_template):
    # Standing on a connected door and WAITing must not re-trigger traversal —
    # only the deliberate step onto the tile does.
    engine = RoomEngine(make_template(spawn_points=[(2, 1)], connections={(2, 0): DEST_ROOM_ID}))
    player, _ = engine.join("Hero")
    submit_move(engine, player.id, [0, -1])  # now standing on the door

    events, _ = engine.submit_action(player.id, {"action_type": "wait"})

    assert EventType.PLAYER_ENTERED_DOOR not in event_types(events)


def test_detach_player_preserves_object_and_clears_grid(make_template):
    room = RoomState(make_template(), seed=1)
    player = room.add_player("Hero")
    pos = (player.position.x, player.position.y)

    detached = room.detach_player(player.id)

    assert detached is player
    assert room.grid[pos[1]][pos[0]] is None
    assert player.id not in room.players
    assert player.id not in room.pending_actions
    assert room.detach_player("player_nope") is None


def test_detach_and_attach_npc_preserve_individual(make_template):
    origin = RoomState(make_template(), seed=1)
    npc = NPC(
        id="npc_44", db_id=44, name="Companion", position=Position(2, 2),
        hp=9, max_hp=10, defense=1, attack_damage=2,
        disposition=Disposition.FRIENDLY, party_owner_id="player_1",
    )
    origin.add_npc(npc)
    destination = RoomState(make_template(), seed=2)

    moved = origin.detach_npc(npc.id)
    destination.attach_npc(moved, Position(3, 2))

    assert moved is npc
    assert npc.id not in origin.npcs
    assert origin.grid[2][2] is None
    assert destination.npcs[npc.id] is npc
    assert destination.grid[2][3] == npc.id


def test_attach_player_places_and_preserves_state(make_template):
    origin = RoomEngine(make_template(connections={(2, 0): DEST_ROOM_ID}))
    player, _ = origin.join("Hero")
    player.hp = 3  # battle-worn — must survive the trip

    dest = RoomEngine(make_template())
    moved = origin.detach_player(player.id)
    events = dest.attach_player(moved)

    assert dest.room.get_player(player.id) is player
    assert player.hp == 3
    # First free spawn of the destination.
    assert (player.position.x, player.position.y) == dest.room.template.spawn_points[0]
    assert EventType.PLAYER_JOINED in event_types(events)
    # Arriving in a dormant room wakes it, same as join().
    assert dest.started and dest.phase == "player_phase"
    assert EventType.ROUND_STARTED in event_types(events)


def test_attach_player_rejects_full_room(make_template):
    dest = RoomEngine(make_template(spawn_points=[(1, 1)]))  # capacity 1
    dest.join("Resident")

    origin = RoomEngine(make_template())
    traveler, _ = origin.join("Hero")
    origin.detach_player(traveler.id)

    with pytest.raises(ValueError):
        dest.attach_player(traveler)


def test_free_spawn_skips_occupied(make_template):
    room = RoomState(make_template(spawn_points=[(1, 1), (2, 1)]), seed=1)
    room.add_enemy("Lurker", Position(1, 1), hp=5, attack_damage=1, defense=0)

    assert room.free_spawn() == (2, 1)

    room.add_enemy("Lurker2", Position(2, 1), hp=5, attack_damage=1, defense=0)
    assert room.free_spawn() is None


def test_detach_last_player_resets_game_phase(make_template):
    engine = RoomEngine(make_template())
    player, _ = engine.join("Hero")
    assert engine.started

    engine.detach_player(player.id)

    assert not engine.started
    assert engine.phase == "waiting"
