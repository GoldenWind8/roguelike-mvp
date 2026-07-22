from backend.actions import Action
from backend.brains import AttackIntent, MoveIntent, select_brain
from backend.effects import apply_effect, Damage, compute_damage
from backend.entities import Actor, NPC, Position
from backend.events import GameEvent, EventType
from backend.handlers import HANDLERS
from backend.inventory import effective_stat
from backend.room_state import RoomState


def validate_player_action(room: RoomState, action: Action) -> GameEvent | None:
    return HANDLERS[action.action_type].validate(room, action)

def resolve_round(room: RoomState, player_actions: dict[str, Action]) -> list[GameEvent]:
    events = []
    order_actions = {}
    actions = list(player_actions.values())

    for action in actions:
        handler = HANDLERS[action.action_type]

        order_actions.setdefault(
            handler.resolve_order,
            []
        ).append(action)

    for order in sorted(order_actions):
        for action in order_actions[order]:
            handler = HANDLERS[action.action_type]

            # Authoritative gate (ARCHITECTURE.md): submission-time validation
            # is advisory only — earlier actions this round may have moved or
            # killed things, so re-check and silently no-op if now illegal.
            if handler.validate(room, action) is not None:
                continue

            events.extend(
                handler.resolve(room, action)
            )

    # --- Actor Phase (enemies chase, followers defend) ---
    events.extend(resolve_actor_phase(room))

    # No win/lose state: this is an infinite, procedurally-generated world, not
    # a match with an end screen. Clearing a room isn't "victory" — it just
    # means no hostiles are present (M7 reads that to flip the room back to
    # exploration). A total-party wipe isn't "defeat" either; death handling
    # (respawn/identity) is its own future concern, not a terminal state here.
    room.round += 1
    events.append(GameEvent(
        EventType.ROUND_STARTED,
        {"round": room.round},
        room.round,
    ))

    return events


def resolve_actor_phase(room: RoomState) -> list[GameEvent]:
    """Every non-player actor that has a brain acts: hostiles chase, followers
    defend. One loop, because behavior is chosen from data (select_brain), not
    from type — an "enemy phase" and a "follower phase" would be the same code
    twice. Players are excluded (their sockets drive them); an actor with no
    brain (a neutral bystander NPC) simply contributes nothing."""
    events = []
    living_players = room.living_players()
    if not living_players:
        return events

    # Enemies then NPCs — order only affects tie-breaks within the two passes.
    actors: list[Actor] = [*room.living_enemies(), *room.living_npcs()]

    moves: list[tuple[Actor, Position]] = []
    attacks: list[tuple[Actor, Actor]] = []

    # Each actor's brain DECIDES (an intent); this phase DISPOSES — resolving it
    # through the shared rules below. Two buckets keep the original ordering
    # (all moves, then all attacks) so an actor can't both close in and strike
    # in one round, and movers don't block each other's chosen tiles until
    # re-checked at resolution (the authoritative-validation habit again).
    for actor in actors:
        brain = select_brain(actor)
        if brain is None:
            continue
        intent = brain.decide(room, actor)
        if isinstance(intent, MoveIntent):
            moves.append((actor, intent.to))
        elif isinstance(intent, AttackIntent):
            target = room.get_entity(intent.target_id)
            if target is not None:
                attacks.append((actor, target))

    for actor, new_pos in moves:
        if not actor.is_alive:
            continue
        if (not room.is_valid_position(new_pos.x, new_pos.y)
                or room.is_occupied(new_pos.x, new_pos.y)):
            continue
        old_pos = [actor.position.x, actor.position.y]
        room.move_entity(actor.id, new_pos)
        events.append(_actor_moved_event(room, actor, old_pos, new_pos))

    for actor, target in attacks:
        if not actor.is_alive or not target.is_alive:
            continue
        if _manhattan(actor.position, target.position) != 1:
            continue
        # Effective, not base: a poison-flask debuff weakens an enemy's swing
        # exactly as a potion strengthens a player's (inventory.effective_stat).
        power = effective_stat(actor, "attack_damage")
        damage = compute_damage(power, target)
        events.append(_actor_attacked_event(room, actor, target, damage))
        events.extend(apply_effect(room, Damage(target.id, power, actor.id)))
    return events


def _manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def _actor_moved_event(room: RoomState, actor: Actor, old_pos: list, new_pos: Position) -> GameEvent:
    # Names never lie: a follower's move is NPC_MOVED, an enemy's is ENEMY_MOVED.
    to = [new_pos.x, new_pos.y]
    if isinstance(actor, NPC):
        return GameEvent(EventType.NPC_MOVED,
                         {"npc_id": actor.id, "name": actor.name, "from": old_pos, "to": to}, room.round)
    return GameEvent(EventType.ENEMY_MOVED,
                     {"enemy_id": actor.id, "name": actor.name, "from": old_pos, "to": to}, room.round)


def _actor_attacked_event(room: RoomState, actor: Actor, target: Actor, damage: int) -> GameEvent:
    etype = EventType.NPC_ATTACKED if isinstance(actor, NPC) else EventType.ENEMY_ATTACKED
    return GameEvent(etype, {
        "attacker_id": actor.id, "attacker_name": actor.name,
        "target_id": target.id, "damage": damage,
    }, room.round)
