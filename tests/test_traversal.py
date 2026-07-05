"""Pure-engine traversal tests: door events and attach/detach.

No DB here — worlds are built from the synthetic `make_level` fixture. The
async edge (socket rewiring, room loading) is covered in test_rooms.py.
"""
from backend.entities import Position
from backend.events import EventType
from backend.game import Game
from backend.world import WorldState

import pytest

DEST_ROOM_ID = 99


def event_types(events):
    return [e.event_type for e in events]


def submit_move(game, player_id, direction):
    return game.submit_action(player_id, {"action_type": "move", "direction": direction})


def test_move_onto_connected_door_emits_event(make_level):
    # Door in the top border at (2, 0), spawn directly below it.
    game = Game(make_level(spawn_points=[(2, 1)], connections={(2, 0): DEST_ROOM_ID}))
    player, _ = game.join("Hero")

    events, resolved = submit_move(game, player.id, [0, -1])

    assert resolved
    door_events = [e for e in events if e.event_type is EventType.PLAYER_ENTERED_DOOR]
    assert len(door_events) == 1
    assert door_events[0].data["player_id"] == player.id
    assert door_events[0].data["to_room_id"] == DEST_ROOM_ID
    assert door_events[0].data["position"] == [2, 0]
    # The move itself is announced before the door intent.
    assert events.index(next(e for e in events if e.event_type is EventType.PLAYER_MOVED)) \
        < events.index(door_events[0])


def test_move_onto_door_without_connection_is_plain_move(make_level):
    game = Game(make_level(spawn_points=[(2, 1)], doors=((2, 0),)))
    player, _ = game.join("Hero")

    events, _ = submit_move(game, player.id, [0, -1])

    assert EventType.PLAYER_MOVED in event_types(events)
    assert EventType.PLAYER_ENTERED_DOOR not in event_types(events)


def test_waiting_on_door_emits_no_event(make_level):
    # Standing on a connected door and WAITing must not re-trigger traversal —
    # only the deliberate step onto the tile does.
    game = Game(make_level(spawn_points=[(2, 1)], connections={(2, 0): DEST_ROOM_ID}))
    player, _ = game.join("Hero")
    submit_move(game, player.id, [0, -1])  # now standing on the door

    events, _ = game.submit_action(player.id, {"action_type": "wait"})

    assert EventType.PLAYER_ENTERED_DOOR not in event_types(events)


def test_detach_player_preserves_object_and_clears_grid(make_level):
    world = WorldState(make_level(), seed=1)
    player = world.add_player("Hero")
    pos = (player.position.x, player.position.y)

    detached = world.detach_player(player.id)

    assert detached is player
    assert world.grid[pos[1]][pos[0]] is None
    assert player.id not in world.players
    assert player.id not in world.pending_actions
    assert world.detach_player("player_nope") is None


def test_attach_player_places_and_preserves_state(make_level):
    origin = Game(make_level(connections={(2, 0): DEST_ROOM_ID}))
    player, _ = origin.join("Hero")
    player.hp = 3  # battle-worn — must survive the trip

    dest = Game(make_level())
    moved = origin.detach_player(player.id)
    events = dest.attach_player(moved)

    assert dest.world.get_player(player.id) is player
    assert player.hp == 3
    # First free spawn of the destination.
    assert (player.position.x, player.position.y) == dest.world.config.spawn_points[0]
    assert EventType.PLAYER_JOINED in event_types(events)
    # Arriving in a dormant room wakes it, same as join().
    assert dest.started and dest.phase == "player_phase"
    assert EventType.ROUND_STARTED in event_types(events)


def test_attach_player_rejects_full_room(make_level):
    dest = Game(make_level(spawn_points=[(1, 1)]))  # capacity 1
    dest.join("Resident")

    origin = Game(make_level())
    traveler, _ = origin.join("Hero")
    origin.detach_player(traveler.id)

    with pytest.raises(ValueError):
        dest.attach_player(traveler)


def test_free_spawn_skips_occupied(make_level):
    world = WorldState(make_level(spawn_points=[(1, 1), (2, 1)]), seed=1)
    world.add_enemy("Lurker", Position(1, 1), hp=5, attack_damage=1, defense=0)

    assert world.free_spawn() == (2, 1)

    world.add_enemy("Lurker2", Position(2, 1), hp=5, attack_damage=1, defense=0)
    assert world.free_spawn() is None


def test_detach_last_player_resets_game_phase(make_level):
    game = Game(make_level())
    player, _ = game.join("Hero")
    assert game.started

    game.detach_player(player.id)

    assert not game.started
    assert game.phase == "waiting"
