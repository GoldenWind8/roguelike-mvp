from dataclasses import dataclass

from backend.room_state import RoomState
from backend.events import GameEvent, EventType
from backend.entities import NPC, Player, Disposition

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

@dataclass
class SetDisposition:
    """Set an actor's disposition toward players. A TRUSTED engine effect: it
    only enters the union after a validator has turned an untrusted proposal
    into one (backend/dialogue_effects.py) — AI proposes, the engine disposes.

    Scope: writes the field and emits an event, nothing else. It deliberately
    does NOT switch room mode or start combat when flipping to hostile — that
    escalation is Milestone 7 (ROADMAP.md), which reads this same field."""
    target_id: str
    disposition: Disposition
    source_id: str | None = None

Effect = Damage | Kill | SetDisposition

def compute_damage(base_amount: int, target) -> int:
    """Damage a target actually takes: base amount minus defense, min 1."""
    return max(1, base_amount - target.defense)

def apply_effect(room: RoomState, effect: Effect) -> list[GameEvent]:
    if isinstance(effect, Damage):
        return _apply_damage(room, effect)
    if isinstance(effect, Kill):
        return _apply_kill(room, effect)
    if isinstance(effect, SetDisposition):
        return _apply_set_disposition(room, effect)
    return []

def _apply_set_disposition(room: RoomState, effect: SetDisposition) -> list[GameEvent]:
    target = room.get_entity(effect.target_id)
    if not target or not target.is_alive:
        return []
    target.disposition = effect.disposition
    return [GameEvent(
        EventType.DISPOSITION_CHANGED,
        {
            "target_id": target.id,
            "disposition": effect.disposition.value,
            "source_id": effect.source_id,
        },
        room.round,
    )]

def _apply_kill(room: RoomState, effect: Kill) -> list[GameEvent]:
    target = room.get_entity(effect.target_id)
    if not target or not target.is_alive:
        return []
    return _kill_entity(room, target, effect.source_id)

def _apply_damage(room: RoomState, effect: Damage) -> list[GameEvent]:
    target = room.get_entity(effect.target_id)
    if not target or not target.is_alive:
        return []
    damage = compute_damage(effect.amount, target)
    target.hp -= damage
    events = [GameEvent(
        EventType.ENTITY_DAMAGED,
        # target_id, not player_id: this path damages enemies too, and the key
        # should never lie about what it holds.
        {"target_id": target.id, "damage": damage, "hp_remaining": max(0, target.hp)},
        room.round,
    )]
    #handles death
    if target.hp <= 0:
        events.extend(_kill_entity(room, target, effect.source_id))

    return events

def _kill_entity(room: RoomState, target, source_id: str | None) -> list[GameEvent]:
    target.hp = 0
    target.is_alive = False
    room.grid[target.position.y][target.position.x] = None

    # Dispatch on actor type, not on a flag: the event name must never lie
    # about what died (an NPC is an individual — its death persists).
    if isinstance(target, Player):
        death_type = EventType.PLAYER_DIED
    elif isinstance(target, NPC):
        death_type = EventType.NPC_DIED
    else:
        death_type = EventType.ENEMY_DIED
    return [GameEvent(
        death_type,
        {"target_id": target.id, "killer_id": source_id},
        room.round,
    )]