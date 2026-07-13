from backend.actions import Action
from backend.config import ENEMY_CHASE_RANGE
from backend.effects import apply_effect, Damage, compute_damage
from backend.entities import Enemy, Player, Position
from backend.events import GameEvent, EventType
from backend.handlers import HANDLERS
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

    # --- Enemy Phase ---
    events.extend(resolve_enemy_phase(room))

    # --- Check game over ---
    living_players = room.living_players()
    if living_players and not room.living_enemies():
        all_enemies_were_present = len(room.enemies) > 0
        if all_enemies_were_present:
            events.append(GameEvent(
                EventType.GAME_OVER,
                {"winner_id": "players", "winner_name": "All players — enemies defeated!"},
                room.round,
            ))
    elif not living_players:
        events.append(GameEvent(
            EventType.GAME_OVER,
            {"winner_id": "enemies", "winner_name": "The dungeon claims all..."},
            room.round,
        ))

    room.round += 1
    events.append(GameEvent(
        EventType.ROUND_STARTED,
        {"round": room.round},
        room.round,
    ))

    return events


def resolve_enemy_phase(room: RoomState) -> list[GameEvent]:
    events = []
    living_enemies = room.living_enemies()
    living_players = room.living_players()
    if not living_players or not living_enemies:
        return events

    enemy_moves: list[tuple[Enemy, Position]] = []
    enemy_attacks: list[tuple[Enemy, Player]] = []

    for enemy in living_enemies:
        nearest_player = _find_nearest_player(enemy, living_players)
        if not nearest_player:
            continue

        dist = abs(enemy.position.x - nearest_player.position.x) + abs(enemy.position.y - nearest_player.position.y)

        if dist == 1:
            enemy_attacks.append((enemy, nearest_player))
        elif dist <= ENEMY_CHASE_RANGE:
            step = _chase_step(room, enemy, nearest_player)
            if step:
                enemy_moves.append((enemy, step))

    # Resolve enemy moves first
    for enemy, new_pos in enemy_moves:
        if not enemy.is_alive:
            continue
        if room.is_occupied(new_pos.x, new_pos.y):
            continue
        old_pos = [enemy.position.x, enemy.position.y]
        room.move_entity(enemy.id, new_pos)
        events.append(GameEvent(
            EventType.ENEMY_MOVED,
            {"enemy_id": enemy.id, "name": enemy.name, "from": old_pos, "to": [new_pos.x, new_pos.y]},
            room.round,
        ))

    # Then resolve enemy attacks
    for enemy, target in enemy_attacks:
        if not enemy.is_alive or not target.is_alive:
            continue
        dist = abs(enemy.position.x - target.position.x) + abs(enemy.position.y - target.position.y)
        if dist != 1:
            continue
        damage = compute_damage(enemy.attack_damage, target)
        events.append(GameEvent(
            EventType.ENEMY_ATTACKED,
            {"attacker_id": enemy.id, "attacker_name": enemy.name, "target_id": target.id, "damage": damage},
            room.round,
        ))

        events.extend(apply_effect(room, Damage(target.id, enemy.attack_damage, enemy.id)))
    return events


def _find_nearest_player(enemy: Enemy, players: list[Player]) -> Player | None:
    best = None
    best_dist = float("inf")
    for p in players:
        dist = abs(enemy.position.x - p.position.x) + abs(enemy.position.y - p.position.y)
        if dist < best_dist:
            best_dist = dist
            best = p
    return best


def _chase_step(room: RoomState, enemy: Enemy, target: Player) -> Position | None:
    dx = target.position.x - enemy.position.x
    dy = target.position.y - enemy.position.y

    candidates = []
    if abs(dx) >= abs(dy):
        candidates.append((1 if dx > 0 else -1, 0))
        if dy != 0:
            candidates.append((0, 1 if dy > 0 else -1))
    else:
        candidates.append((0, 1 if dy > 0 else -1))
        if dx != 0:
            candidates.append((1 if dx > 0 else -1, 0))

    for sx, sy in candidates:
        nx, ny = enemy.position.x + sx, enemy.position.y + sy
        if room.is_valid_position(nx, ny) and not room.is_occupied(nx, ny):
            return Position(nx, ny)
    return None
