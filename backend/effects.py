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

@dataclass
class JoinParty:
    """Recruit an NPC into a player's party. A TRUSTED engine effect, like
    SetDisposition: it only exists after the dialogue validator turned an
    untrusted `join_party` proposal into one (backend/dialogue_effects.py),
    having checked the NPC is willing (grants), warm (friendly), free, and the
    owner is under the cap. Writes `party_owner_id` and emits the event."""
    target_id: str
    owner_id: str
    source_id: str | None = None


@dataclass
class LeaveParty:
    """Dissolve an NPC's party membership (it quits, or is dismissed). Clears
    `party_owner_id`. Idempotent: leaving when not in a party is a silent
    no-op, never an error."""
    target_id: str
    source_id: str | None = None


@dataclass
class Heal:
    """Restore hp, clamped to the target's EFFECTIVE max_hp at apply time.
    Damage's happier twin: items are the first tenant (a drunk potion, a
    thrown healing flask), but nothing here is item-shaped — a future shrine
    or an NPC's blessing emits this same effect."""
    target_id: str
    amount: int
    source_id: str | None = None


@dataclass
class RestoreHunger:
    """Refill a hunger meter, clamped to its ceiling. Heal's kitchen twin.
    Deliberately a silent no-op on actors WITHOUT a hunger field (enemies):
    the restore_hunger atom is generic data, and what it means is the
    target's business — throwing bread at a skeleton wastes the bread."""
    target_id: str
    amount: int
    source_id: str | None = None


@dataclass
class TimedStat:
    """A stat change that rides the world clock (docs/LOOT.md Decision 3):
    lands as an active_effect on the target and falls off at expires_at.
    `source` is a human label ("Potion of Fury") for the client's buff list."""
    target_id: str
    stat: str
    amount: int
    duration_s: float
    source: str
    source_id: str | None = None


Effect = Damage | Kill | SetDisposition | JoinParty | LeaveParty | Heal | RestoreHunger | TimedStat

def compute_damage(base_amount: int, target) -> int:
    """Damage a target actually takes: base amount minus EFFECTIVE defense
    (base + equipped armor + timed effects, backend/inventory.py), min 1 —
    so a poison-softened goblin and an armored player both resolve here."""
    from backend.inventory import effective_stat
    return max(1, base_amount - effective_stat(target, "defense"))

def apply_effect(room: RoomState, effect: Effect) -> list[GameEvent]:
    if isinstance(effect, Damage):
        return _apply_damage(room, effect)
    if isinstance(effect, Kill):
        return _apply_kill(room, effect)
    if isinstance(effect, SetDisposition):
        return _apply_set_disposition(room, effect)
    if isinstance(effect, JoinParty):
        return _apply_join_party(room, effect)
    if isinstance(effect, LeaveParty):
        return _apply_leave_party(room, effect)
    if isinstance(effect, Heal):
        return _apply_heal(room, effect)
    if isinstance(effect, RestoreHunger):
        return _apply_restore_hunger(room, effect)
    if isinstance(effect, TimedStat):
        return _apply_timed_stat(room, effect)
    return []


def _apply_heal(room: RoomState, effect: Heal) -> list[GameEvent]:
    from backend.inventory import effective_stat
    target = room.get_entity(effect.target_id)
    if not target or not target.is_alive:
        return []
    ceiling = effective_stat(target, "max_hp")
    healed = max(0, min(effect.amount, ceiling - target.hp))
    target.hp += healed
    return [GameEvent(
        EventType.ENTITY_HEALED,
        {"target_id": target.id, "amount": healed, "hp": target.hp,
         "source_id": effect.source_id},
        room.round,
    )]


def _apply_restore_hunger(room: RoomState, effect: RestoreHunger) -> list[GameEvent]:
    from backend.config import HUNGER_MAX
    target = room.get_entity(effect.target_id)
    if not target or not target.is_alive or not hasattr(target, "hunger"):
        return []
    fed = max(0, min(effect.amount, HUNGER_MAX - target.hunger))
    target.hunger += fed
    return [GameEvent(
        EventType.HUNGER_RESTORED,
        {"target_id": target.id, "amount": round(fed),
         "hunger": round(target.hunger), "source_id": effect.source_id},
        room.round,
    )]


def _apply_timed_stat(room: RoomState, effect: TimedStat) -> list[GameEvent]:
    from backend.inventory import add_timed_effect
    target = room.get_entity(effect.target_id)
    if not target or not target.is_alive:
        return []
    add_timed_effect(target, effect.stat, effect.amount, effect.duration_s, effect.source)
    return [GameEvent(
        EventType.EFFECT_APPLIED,
        {"target_id": target.id, "stat": effect.stat, "amount": effect.amount,
         "duration_s": effect.duration_s, "source": effect.source,
         "source_id": effect.source_id},
        room.round,
    )]

def _apply_set_disposition(room: RoomState, effect: SetDisposition) -> list[GameEvent]:
    target = room.get_entity(effect.target_id)
    if not target or not target.is_alive:
        return []
    target.disposition = effect.disposition
    events = [GameEvent(
        EventType.DISPOSITION_CHANGED,
        {
            "target_id": target.id,
            "disposition": effect.disposition.value,
            "source_id": effect.source_id,
        },
        room.round,
    )]
    # Invariant: a follower is always friendly. An ally soured to neutral or
    # hostile stops being your ally — the same field write that recolors it also
    # dissolves the party bond (else select_brain would hand your "follower" the
    # ChaseBrain and it would hunt you). Effects composing, not special cases.
    if effect.disposition is not Disposition.FRIENDLY and getattr(target, "party_owner_id", None):
        events.extend(_apply_leave_party(room, LeaveParty(target_id=target.id, source_id=effect.source_id)))
    return events

def _apply_join_party(room: RoomState, effect: JoinParty) -> list[GameEvent]:
    npc = room.get_entity(effect.target_id)
    if not isinstance(npc, NPC) or not npc.is_alive:
        return []
    npc.party_owner_id = effect.owner_id
    return [GameEvent(
        EventType.PARTY_CHANGED,
        {"target_id": npc.id, "owner_id": effect.owner_id, "source_id": effect.source_id},
        room.round,
    )]

def _apply_leave_party(room: RoomState, effect: LeaveParty) -> list[GameEvent]:
    npc = room.get_entity(effect.target_id)
    if not isinstance(npc, NPC) or not npc.party_owner_id:
        return []
    npc.party_owner_id = None
    return [GameEvent(
        EventType.PARTY_CHANGED,
        {"target_id": npc.id, "owner_id": None, "source_id": effect.source_id},
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