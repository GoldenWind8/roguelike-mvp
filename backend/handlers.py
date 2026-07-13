from abc import ABC, abstractmethod

from backend.actions import ActionType, Action
from backend.config import BOMB_THROW_RANGE, BOMB_RADIUS, BOMB_DAMAGE
from backend.effects import apply_effect, compute_damage, Damage
from backend.entities import Position
from backend.room_state import RoomState
from backend.events import GameEvent, EventType

MOVE_ORDER = 0
ACTION_ORDER = 1

def _invalid(room: RoomState, reason: str) -> GameEvent:
    return GameEvent(EventType.INVALID_ACTION, {"reason": reason}, room.round)

def _manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)

class ActionHandler(ABC):
    # Resolution ordering within a round (moves before acts). Named
    # resolve_order, NOT phase — "phase" already means the engine lifecycle
    # state ("waiting"/"player_phase"), and one word must not mean two things.
    resolve_order: int

    @abstractmethod
    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        pass
    @abstractmethod
    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        pass

class MoveHandler(ActionHandler):
    resolve_order = MOVE_ORDER
    def validate(self, room: RoomState, action : Action) -> GameEvent | None:
            player = room.get_player(action.player_id)
            if not player or not player.is_alive:
                return _invalid(room, f"Player {action.player_id} is not found or dead")
            if not action.direction:
                return _invalid(room, "No direction")
            nx = player.position.x + action.direction[0]
            ny = player.position.y + action.direction[1]
            if not room.is_valid_position(nx, ny):
                return _invalid(room, "Can't move there")
            if room.is_occupied(nx, ny):
                return _invalid(room, "Is occupied")
            return None

    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        player = room.get_player(action.player_id)
        old_pos = [player.position.x, player.position.y]
        nx = player.position.x + action.direction[0]
        ny = player.position.y + action.direction[1]
        new_pos = Position(nx, ny)
        room.move_entity(action.player_id, new_pos)

        events = [GameEvent(
            EventType.PLAYER_MOVED,
            {"player_id": action.player_id, "from": old_pos, "to": [nx, ny]},
            room.round,
        )]

        # Stepping onto a connected door/portal is intent to traverse. The
        # engine only announces it — the async edge owns the actual transfer
        # (DB load, capacity checks, socket rewiring). Emitting on the step
        # (not on standing) means a denied traversal doesn't retry every round.
        to_room_id = room.template.connections.get((nx, ny))
        if to_room_id is not None:
            events.append(GameEvent(
                EventType.PLAYER_ENTERED_DOOR,
                # name included because by broadcast time the player is no
                # longer in the old room's state for clients to look up.
                {"player_id": action.player_id, "name": player.name,
                 "position": [nx, ny], "to_room_id": to_room_id},
                room.round,
            ))

        return events


class AttackHandler(ActionHandler):
    resolve_order = ACTION_ORDER
    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        player = room.get_player(action.player_id)
        if not player or not player.is_alive:
            return _invalid(room, f"Player {action.player_id} is not found or dead")
        if not action.target_id:
            return _invalid(room, "No target")
        target = room.get_entity(action.target_id)
        if not target or not target.is_alive:
            return _invalid(room, f"Target {action.target_id} is not found or dead")
        if _manhattan(player.position, target.position) != 1:
            return _invalid(room,  "Target not adjacent")
        return None

    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        player = room.get_player(action.player_id)
        target = room.get_entity(action.target_id)
        damage = compute_damage(player.attack_damage, target)

        events = [GameEvent(
            EventType.PLAYER_ATTACKED,
            {"attacker_id": player.id, "target_id": target.id, "damage": damage},
            room.round,
        )]
        events.extend(apply_effect(room, Damage(target.id, player.attack_damage, player.id)))
        return events

class WaitHandler(ActionHandler):
    resolve_order = ACTION_ORDER
    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        return None
    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        return []

class BombHandler(ActionHandler):
    resolve_order = ACTION_ORDER
    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        player = room.get_player(action.player_id)
        if not player or not player.is_alive:
            return _invalid(room, f"Player {action.player_id} is not found or dead")
        if not action.target_tile:
            return _invalid(room, "No target tile")
        tx, ty = action.target_tile
        if not room.is_valid_position(tx, ty):
            return _invalid(room, "Can't target there")
        if _manhattan(player.position, Position(tx, ty)) > BOMB_THROW_RANGE:
            return _invalid(room, "Out of throwing range")
        return None

    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        player = room.get_player(action.player_id)
        tx, ty = action.target_tile
        center = Position(tx, ty)
        events = [GameEvent(
            EventType.BOMB_THROWN,
            {"player_id": player.id, "tile": [tx, ty], "radius": BOMB_RADIUS},
            room.round,
        )]
        # Friendly fire is intentional: the blast hits every living entity in
        # radius, including the thrower and allies. Defense/clamp math lives in
        # apply_effect, so the handler only emits intent.
        for entity in [*room.living_players(), *room.living_enemies()]:
            if _manhattan(center, entity.position) <= BOMB_RADIUS:
                events.extend(apply_effect(room, Damage(entity.id, BOMB_DAMAGE, source_id=player.id)))
        return events


HANDLERS: dict[ActionType, ActionHandler] = {
    ActionType.MOVE: MoveHandler(),
    ActionType.WAIT: WaitHandler(),
    ActionType.ATTACK: AttackHandler(),
    ActionType.BOMB: BombHandler(),
}
