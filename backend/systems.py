from backend.actions import Action
from backend.config import ENEMY_CHASE_RANGE
from backend.effects import apply_effect, Damage, compute_damage
from backend.entities import Enemy, Player, Position
from backend.events import GameEvent, EventType
from backend.handlers import HANDLERS
from backend.world import WorldState


def validate_player_action(world: WorldState, action: Action) -> GameEvent | None:
    return HANDLERS[action.action_type].validate(world, action)

def resolve_round(world: WorldState, player_actions: dict[str, Action]) -> list[GameEvent]:
    events = []
    phase_actions = {}
    actions = list(player_actions.values())

    for action in actions:
        handler = HANDLERS[action.action_type]

        phase_actions.setdefault(
            handler.phase,
            []
        ).append(action)

    for phase in sorted(phase_actions):
        for action in phase_actions[phase]:
            handler = HANDLERS[action.action_type]

            events.extend(
                handler.resolve(world, action)
            )

    # --- Enemy Phase ---
    events.extend(resolve_enemy_phase(world))

    # --- Check game over ---
    living_players = world.living_players()
    if living_players and not world.living_enemies():
        all_enemies_were_present = len(world.enemies) > 0
        if all_enemies_were_present:
            events.append(GameEvent(
                EventType.GAME_OVER,
                {"winner_id": "players", "winner_name": "All players — enemies defeated!"},
                world.round,
            ))
    elif not living_players:
        events.append(GameEvent(
            EventType.GAME_OVER,
            {"winner_id": "enemies", "winner_name": "The dungeon claims all..."},
            world.round,
        ))

    world.round += 1
    events.append(GameEvent(
        EventType.ROUND_STARTED,
        {"round": world.round},
        world.round,
    ))

    return events


def resolve_enemy_phase(world: WorldState) -> list[GameEvent]:
    events = []
    living_enemies = world.living_enemies()
    living_players = world.living_players()
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
            step = _chase_step(world, enemy, nearest_player)
            if step:
                enemy_moves.append((enemy, step))

    # Resolve enemy moves first
    for enemy, new_pos in enemy_moves:
        if not enemy.is_alive:
            continue
        if world.is_occupied(new_pos.x, new_pos.y):
            continue
        old_pos = [enemy.position.x, enemy.position.y]
        world.move_entity(enemy.id, new_pos)
        events.append(GameEvent(
            EventType.ENEMY_MOVED,
            {"enemy_id": enemy.id, "name": enemy.name, "from": old_pos, "to": [new_pos.x, new_pos.y]},
            world.round,
        ))

    # Then resolve enemy attacks
    for enemy, target in enemy_attacks:
        if not enemy.is_alive or not target.is_alive:
            continue
        dist = abs(enemy.position.x - target.position.x) + abs(enemy.position.y - target.position.y)
        if dist != 1:
            continue
        damage = compute_damage(enemy, target)
        events.append(GameEvent(
            EventType.ENEMY_ATTACKED,
            {"attacker_id": enemy.id, "attacker_name": enemy.name, "target_id": target.id, "damage": damage},
            world.round,
        ))

        events.extend(apply_effect(world, Damage(target.id, enemy.attack_damage, enemy.id)))
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


def _chase_step(world: WorldState, enemy: Enemy, target: Player) -> Position | None:
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
        if world.is_valid_position(nx, ny) and not world.is_occupied(nx, ny):
            return Position(nx, ny)
    return None
