from backend.actions import Action, ActionType
from backend.config import PLAYER_ATTACK_DAMAGE
from backend.entities import Position
from backend.events import GameEvent, EventType
from backend.world import WorldState


def validate_action(world: WorldState, action: Action) -> GameEvent | None:
    player = world.get_player(action.player_id)
    if not player or not player.is_alive:
        return GameEvent(EventType.INVALID_ACTION, {"reason": "Player not found or dead"}, world.tick)

    if world.current_player_id() != action.player_id:
        return GameEvent(EventType.INVALID_ACTION, {"reason": "Not your turn"}, world.tick)

    if action.action_type == ActionType.MOVE:
        if not action.direction:
            return GameEvent(EventType.INVALID_ACTION, {"reason": "No direction"}, world.tick)
        nx = player.position.x + action.direction[0]
        ny = player.position.y + action.direction[1]
        if not world.is_valid_position(nx, ny):
            return GameEvent(EventType.INVALID_ACTION, {"reason": "Can't move there"}, world.tick)
        if world.is_occupied(nx, ny):
            return GameEvent(EventType.INVALID_ACTION, {"reason": "Space is occupied"}, world.tick)

    elif action.action_type == ActionType.ATTACK:
        if not action.target_id:
            return GameEvent(EventType.INVALID_ACTION, {"reason": "No target"}, world.tick)
        target = world.get_player(action.target_id)
        if not target or not target.is_alive:
            return GameEvent(EventType.INVALID_ACTION, {"reason": "Target not found or dead"}, world.tick)
        dx = abs(player.position.x - target.position.x)
        dy = abs(player.position.y - target.position.y)
        if dx + dy != 1:
            return GameEvent(EventType.INVALID_ACTION, {"reason": "Target not adjacent"}, world.tick)

    return None


def process_move(world: WorldState, action: Action) -> list[GameEvent]:
    player = world.get_player(action.player_id)
    old_pos = [player.position.x, player.position.y]
    new_pos = Position(
        player.position.x + action.direction[0],
        player.position.y + action.direction[1],
    )
    world.move_entity(action.player_id, new_pos)
    return [GameEvent(
        EventType.PLAYER_MOVED,
        {"player_id": action.player_id, "from": old_pos, "to": [new_pos.x, new_pos.y]},
        world.tick,
    )]


def process_attack(world: WorldState, action: Action) -> list[GameEvent]:
    attacker = world.get_player(action.player_id)
    target = world.get_player(action.target_id)
    damage = PLAYER_ATTACK_DAMAGE
    events = []

    events.append(GameEvent(
        EventType.PLAYER_ATTACKED,
        {"attacker_id": attacker.id, "target_id": target.id, "damage": damage},
        world.tick,
    ))

    target.hp -= damage

    events.append(GameEvent(
        EventType.PLAYER_DAMAGED,
        {"player_id": target.id, "damage": damage, "hp_remaining": target.hp},
        world.tick,
    ))

    if target.hp <= 0:
        target.hp = 0
        target.is_alive = False
        world.grid[target.position.y][target.position.x] = None
        events.append(GameEvent(
            EventType.PLAYER_DIED,
            {"player_id": target.id, "killer_id": attacker.id},
            world.tick,
        ))

        living = world.living_players()
        if len(living) == 1:
            events.append(GameEvent(
                EventType.GAME_OVER,
                {"winner_id": living[0].id, "winner_name": living[0].name},
                world.tick,
            ))

    return events


def process_action(world: WorldState, action: Action) -> list[GameEvent]:
    error = validate_action(world, action)
    if error:
        return [error]

    if action.action_type == ActionType.MOVE:
        events = process_move(world, action)
    elif action.action_type == ActionType.ATTACK:
        events = process_attack(world, action)
    else:
        return [GameEvent(EventType.INVALID_ACTION, {"reason": "Unknown action"}, world.tick)]

    world.advance_turn()
    events.append(GameEvent(
        EventType.TURN_STARTED,
        {"player_id": world.current_player_id()},
        world.tick,
    ))

    world.event_log = getattr(world, 'event_log', [])
    world.event_log.extend(events)
    return events
