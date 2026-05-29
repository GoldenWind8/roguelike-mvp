from backend.actions import Action, ActionType
from backend.config import PLAYER_ATTACK_DAMAGE, ENEMY_CHASE_RANGE, PLAYER_DEFENSE
from backend.entities import Enemy, Player, Position
from backend.events import GameEvent, EventType
from backend.world import WorldState


def validate_player_action(world: WorldState, action: Action) -> GameEvent | None:
    player = world.get_player(action.player_id)
    if not player or not player.is_alive:
        return GameEvent(EventType.INVALID_ACTION, {"reason": "Player not found or dead"}, world.round)

    if action.action_type == ActionType.WAIT:
        return None

    if action.action_type == ActionType.MOVE:
        if not action.direction:
            return GameEvent(EventType.INVALID_ACTION, {"reason": "No direction"}, world.round)
        nx = player.position.x + action.direction[0]
        ny = player.position.y + action.direction[1]
        if not world.is_valid_position(nx, ny):
            return GameEvent(EventType.INVALID_ACTION, {"reason": "Can't move there"}, world.round)

    elif action.action_type == ActionType.ATTACK:
        if not action.target_id:
            return GameEvent(EventType.INVALID_ACTION, {"reason": "No target"}, world.round)
        target = world.get_entity(action.target_id)
        if not target or not target.is_alive:
            return GameEvent(EventType.INVALID_ACTION, {"reason": "Target not found or dead"}, world.round)
        dx = abs(player.position.x - target.position.x)
        dy = abs(player.position.y - target.position.y)
        if dx + dy != 1:
            return GameEvent(EventType.INVALID_ACTION, {"reason": "Target not adjacent"}, world.round)

    return None


def resolve_round(world: WorldState, player_actions: dict[str, Action]) -> list[GameEvent]:
    events = []

    # --- Player Phase: moves first, then attacks ---
    move_actions = []
    attack_actions = []
    for pid, action in player_actions.items():
        if action.action_type == ActionType.MOVE:
            move_actions.append(action)
        elif action.action_type == ActionType.ATTACK:
            attack_actions.append(action)

    for action in move_actions:
        player = world.get_player(action.player_id)
        if not player or not player.is_alive:
            continue
        nx = player.position.x + action.direction[0]
        ny = player.position.y + action.direction[1]
        if not world.is_valid_position(nx, ny):
            continue
        if world.is_occupied(nx, ny):
            continue
        old_pos = [player.position.x, player.position.y]
        new_pos = Position(nx, ny)
        world.move_entity(action.player_id, new_pos)
        events.append(GameEvent(
            EventType.PLAYER_MOVED,
            {"player_id": action.player_id, "from": old_pos, "to": [nx, ny]},
            world.round,
        ))

    for action in attack_actions:
        player = world.get_player(action.player_id)
        if not player or not player.is_alive:
            continue
        target = world.get_entity(action.target_id)
        if not target or not target.is_alive:
            continue
        dx = abs(player.position.x - target.position.x)
        dy = abs(player.position.y - target.position.y)
        if dx + dy != 1:
            continue
        damage = max(1, PLAYER_ATTACK_DAMAGE -  PLAYER_DEFENSE)
        events.append(GameEvent(
            EventType.PLAYER_ATTACKED,
            {"attacker_id": player.id, "target_id": target.id, "damage": damage},
            world.round,
        ))
        target.hp -= damage
        events.append(GameEvent(
            EventType.PLAYER_DAMAGED,
            {"player_id": target.id, "damage": damage, "hp_remaining": max(0, target.hp)},
            world.round,
        ))
        if target.hp <= 0:
            target.hp = 0
            target.is_alive = False
            world.grid[target.position.y][target.position.x] = None
            death_type = EventType.ENEMY_DIED if isinstance(target, Enemy) else EventType.PLAYER_DIED
            events.append(GameEvent(
                death_type,
                {"player_id": target.id, "killer_id": player.id},
                world.round,
            ))

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
        damage = max(1, enemy.attack_damage - PLAYER_DEFENSE )
        events.append(GameEvent(
            EventType.ENEMY_ATTACKED,
            {"attacker_id": enemy.id, "attacker_name": enemy.name, "target_id": target.id, "damage": damage},
            world.round,
        ))
        target.hp -= damage
        events.append(GameEvent(
            EventType.PLAYER_DAMAGED,
            {"player_id": target.id, "damage": damage, "hp_remaining": max(0, target.hp)},
            world.round,
        ))
        if target.hp <= 0:
            target.hp = 0
            target.is_alive = False
            world.grid[target.position.y][target.position.x] = None
            events.append(GameEvent(
                EventType.PLAYER_DIED,
                {"player_id": target.id, "killer_id": enemy.id},
                world.round,
            ))

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
