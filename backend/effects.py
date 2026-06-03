from dataclasses import dataclass

from backend.world import WorldState
from backend.events import GameEvent, EventType
from backend.entities import Enemy


@dataclass
class Damage:
    target_id: str
    amount: int
    source_id: str | None = None

Effect = Damage

def compute_damage(attacker, target) -> int:
    return max(1, attacker.attack_damage - target.defense)

def apply_effect(world: WorldState, effect: Effect) -> list[GameEvent]:
    if isinstance(effect, Damage):
        return _apply_damage(world, effect)
    return []

def _apply_damage(world: WorldState, effect: Damage) -> list[GameEvent]:
    target = world.get_entity(effect.target_id)
    damage = effect.amount
    if not target or not target.is_alive:
        return []
    target.hp -= damage
    events = [GameEvent(
        EventType.PLAYER_DAMAGED,
        {"player_id": target.id, "damage": damage, "hp_remaining": max(0, target.hp)},
        world.round,
    )]
    #handles death
    if target.hp <= 0:
        target.hp = 0
        target.is_alive = False
        world.grid[target.position.y][target.position.x] = None   # free the tile
        death_type = EventType.ENEMY_DIED if isinstance(target, Enemy) else EventType.PLAYER_DIED
        events.append(GameEvent(
            death_type,
            {"player_id": target.id, "killer_id": effect.source_id},   # may be None
            world.round,
        ))

    return events
