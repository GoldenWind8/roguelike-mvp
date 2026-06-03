from abc import ABC, abstractmethod

from backend.actions import ActionType, Action
from backend.config import BOMB_THROW_RANGE, BOMB_RADIUS, BOMB_DAMAGE
from backend.effects import apply_effect, compute_damage, Damage
from backend.entities import Position
from backend.world import WorldState
from backend.events import GameEvent, EventType

MOVE_PHASE = 0
ACTION_PHASE = 1

def _invalid(world: WorldState, reason: str) -> GameEvent:
    return GameEvent(EventType.INVALID_ACTION, {"reason": reason}, world.round)

def _manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)

class ActionHandler(ABC):
    phase: int

    @abstractmethod
    def validate(self, world: WorldState, action: Action) -> GameEvent | None:
        pass
    @abstractmethod
    def resolve(self, world: WorldState, action: Action) -> list[GameEvent]:
        pass

class MoveHandler(ActionHandler):
    phase = MOVE_PHASE
    def validate(self, world: WorldState, action : Action) -> GameEvent | None:
            player = world.get_player(action.player_id)
            if not player or not player.is_alive:
                return _invalid(world, f"Player {action.player_id} is not found or dead")
            if not action.direction:
                return _invalid(world, "No direction")
            nx = player.position.x + action.direction[0]
            ny = player.position.y + action.direction[1]
            if not world.is_valid_position(nx, ny):
                return _invalid(world, "Can't move there")
            if world.is_occupied(nx, ny):
                return _invalid(world, "Is occupied")
            return None

    def resolve(self, world: WorldState, action: Action) -> list[GameEvent]:
        player = world.get_player(action.player_id)
        old_pos = [player.position.x, player.position.y]
        nx = player.position.x + action.direction[0]
        ny = player.position.y + action.direction[1]
        new_pos = Position(nx, ny)
        world.move_entity(action.player_id, new_pos)

        return [GameEvent(
            EventType.PLAYER_MOVED,
            {"player_id": action.player_id, "from": old_pos, "to": [nx, ny]},
            world.round,
        )]


class AttackHandler(ActionHandler):
    phase = ACTION_PHASE
    def validate(self, world: WorldState, action: Action) -> GameEvent | None:
        player = world.get_player(action.player_id)
        if not player or not player.is_alive:
            return _invalid(world, f"Player {action.player_id} is not found or dead")
        if not action.target_id:
            return _invalid(world, "No target")
        target = world.get_entity(action.target_id)
        if not target or not target.is_alive:
            return _invalid(world, f"Target {action.target_id} is not found or dead")
        if _manhattan(player.position, target.position) != 1:
            return _invalid(world,  "Target not adjacent")
        return None

    def resolve(self, world: WorldState, action: Action) -> list[GameEvent]:
        player = world.get_player(action.player_id)
        target = world.get_entity(action.target_id)
        damage = compute_damage(player, target)

        events = [GameEvent(
            EventType.PLAYER_ATTACKED,
            {"attacker_id": player.id, "target_id": target.id, "damage": damage},
             world.round,
        )]
        events.extend(apply_effect(world, Damage(target.id, damage, player.id)))
        return events

class WaitHandler(ActionHandler):
    phase = ACTION_PHASE
    def validate(self, world: WorldState, action: Action) -> GameEvent | None:
        return None
    def resolve(self, world: WorldState, action: Action) -> list[GameEvent]:
        return []

class BombHandler(ActionHandler):
    phase = ACTION_PHASE
    def validate(self, world: WorldState, action: Action) -> GameEvent | None:
        player = world.get_player(action.player_id)
        if not player or not player.is_alive:
            return _invalid(world, f"Player {action.player_id} is not found or dead")
        if not action.target_tile:
            return _invalid(world, "No target tile")
        tx, ty = action.target_tile
        if not world.is_valid_position(tx, ty):
            return _invalid(world, "Can't target there")
        player = world.get_player(action.player_id)
        if _manhattan(player.position, Position(tx, ty)) > BOMB_THROW_RANGE:
            return _invalid(world, "Out of throwing range")
        return None

    def resolve(self, world: WorldState, action: Action) -> list[GameEvent]:
        player = world.get_player(action.player_id)
        if not player or not player.is_alive:
            return []
        tx, ty = action.target_tile
        center = Position(tx, ty)
        events = [GameEvent(
            EventType.BOMB_THROWN,
            {"player_id": player.id, "tile": [tx, ty], "radius": BOMB_RADIUS},
            world.round,
        )]
        for entity in [*world.living_players(), *world.living_enemies()]:
            if _manhattan(center, entity.position) <= BOMB_RADIUS:
                amount = max(1, BOMB_DAMAGE - entity.defense)
                events.extend(apply_effect(world, Damage(entity.id, amount, source_id=player.id)))
        return events


HANDLERS: dict[ActionType, ActionHandler] = {
    ActionType.MOVE: MoveHandler(),
    ActionType.WAIT: WaitHandler(),
    ActionType.ATTACK: AttackHandler(),
    ActionType.BOMB: BombHandler(),
}