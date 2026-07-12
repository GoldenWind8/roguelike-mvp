from dataclasses import dataclass

from backend.world import WorldState
from backend.events import GameEvent, EventType
from backend.entities import Enemy

@dataclass
class Kill:
    """Unconditional death, ignores defense.
    _apply_kill is the single place where death side effects (e.g. grid tile cleanup and is_alive updates) happen."""
    target_id: str
    source_id: str | None = None

@dataclass
class Damage:
    """Intent to damage a target. `amount` is the BASE damage before defense —
    the defense subtraction and the min-1 clamp happen centrally in
    `_apply_damage`, so no action/handler can bypass the engine clamp."""
    target_id: str
    amount: int
    source_id: str | None = None

Effect = Damage | Kill

def compute_damage(base_amount: int, target) -> int:
    """Damage a target actually takes: base amount minus defense, min 1."""
    return max(1, base_amount - target.defense)

def apply_effect(world: WorldState, effect: Effect) -> list[GameEvent]:
    if isinstance(effect, Damage):
        return _apply_damage(world, effect)
    if isinstance(effect, Kill):
        return _apply_kill(world, effect)
    return []

def _apply_kill(world: WorldState, effect: Kill) -> list[GameEvent]:
    target = world.get_entity(effect.target_id)
    if not target or not target.is_alive:
        return []
    return _kill_entity(world, target, effect.source_id)

def _apply_damage(world: WorldState, effect: Damage) -> list[GameEvent]:
    target = world.get_entity(effect.target_id)
    if not target or not target.is_alive:
        return []
    damage = compute_damage(effect.amount, target)
    target.hp -= damage
    events = [GameEvent(
        EventType.PLAYER_DAMAGED,
        {"player_id": target.id, "damage": damage, "hp_remaining": max(0, target.hp)},
        world.round,
    )]
    #handles death
    if target.hp <= 0:
        events.extend(_kill_entity(world, target, effect.source_id))

    return events

def _kill_entity(world: WorldState, target, source_id: str | None) -> list[GameEvent]:
    target.hp = 0
    target.is_alive = False
    world.grid[target.position.y][target.position.x] = None

    death_type = EventType.ENEMY_DIED if isinstance(target, Enemy) else EventType.PLAYER_DIED
    return [GameEvent(
        death_type,
        {"player_id": target.id, "killer_id": source_id},
        world.round,
    )]