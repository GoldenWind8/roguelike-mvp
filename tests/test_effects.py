"""The Kill effect through apply_effect (the unconditional-death path).

Modernized from a pre-room-rename version that imported the long-gone
backend.world.WorldState — Kill still exists in the effect union, so it keeps
its own coverage here against current RoomState conventions.
"""
from backend.effects import apply_effect, Kill
from backend.entities import Position
from backend.events import EventType
from backend.room_state import RoomState


def _room(make_template):
    return RoomState(make_template(), seed=1)


def test_kill_kills_enemy(make_template):
    room = _room(make_template)
    enemy = room.add_enemy(name="Rat", position=Position(3, 3), hp=10, attack_damage=5, defense=0)
    events = apply_effect(room, Kill(enemy.id))

    assert enemy.is_alive is False
    assert enemy.hp == 0
    assert room.grid[enemy.position.y][enemy.position.x] is None
    assert events[0].event_type == EventType.ENEMY_DIED
    assert events[0].data["target_id"] == enemy.id


def test_kill_kills_player(make_template):
    room = _room(make_template)
    player = room.add_player(name="gira")
    events = apply_effect(room, Kill(player.id))

    assert room.grid[player.position.y][player.position.x] is None
    assert player.is_alive is False
    assert player.hp == 0
    assert events[0].event_type == EventType.PLAYER_DIED
    assert events[0].data["target_id"] == player.id


def test_kill_on_dead_target_is_noop(make_template):
    room = _room(make_template)
    player = room.add_player(name="pookie")

    apply_effect(room, Kill(player.id))
    second_events = apply_effect(room, Kill(player.id))

    assert second_events == []
    assert player.is_alive is False
    assert player.hp == 0
