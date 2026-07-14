from backend.effects import apply_effect, Kill
from backend.entities import Position
from backend.events import EventType
from backend.world import WorldState


def test_kill_kills_enemy(make_level):
    world = WorldState(make_level(), seed=1)
    enemy = world.add_enemy(name="Rat", position=Position(3, 3), hp=10, attack_damage=5, defense=0)
    events = apply_effect(world, Kill(enemy.id))

    assert enemy.is_alive is False
    assert enemy.hp == 0
    assert world.grid[enemy.position.x][enemy.position.y] is None
    assert events[0].event_type == EventType.ENEMY_DIED
    assert events[0].data['player_id'] == enemy.id

def test_kill_kills_player(make_level):
    world = WorldState(make_level(), seed=1)
    player = world.add_player(name='gira')
    events = apply_effect(world, Kill(player.id))

    assert world.grid[player.position.x][player.position.y] is None
    assert player.is_alive is False
    assert player.hp == 0
    assert events[0].event_type == EventType.PLAYER_DIED
    assert events[0].data['player_id'] == player.id

def test_kill_on_dead_target_is_noop(make_level):
    world = WorldState(make_level(), seed=1)
    player = world.add_player(name='pookie')

    first_events = apply_effect(world, Kill(player.id))
    second_events = apply_effect(world, Kill(player.id))

    assert second_events == []

    assert world.grid[player.position.x][player.position.y] is None
    assert player.is_alive is False
    assert player.hp == 0


