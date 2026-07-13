from dataclasses import dataclass
from enum import Enum


class EventType(Enum):
    PLAYER_JOINED = "player_joined"
    PLAYER_MOVED = "player_moved"
    PLAYER_ATTACKED = "player_attacked"
    # Damage lands on players AND enemies through the same effect path, so the
    # event is entity-scoped, not player-scoped (its data key is `target_id`).
    ENTITY_DAMAGED = "entity_damaged"
    PLAYER_DIED = "player_died"
    ENEMY_MOVED = "enemy_moved"
    ENEMY_ATTACKED = "enemy_attacked"
    ENEMY_DIED = "enemy_died"
    ROUND_STARTED = "round_started"
    INVALID_ACTION = "invalid_action"
    PLAYER_LEFT = "player_left"
    PLAYER_ENTERED_DOOR = "player_entered_door"
    GAME_OVER = "game_over"
    BOMB_THROWN = "bomb_thrown"


@dataclass
class GameEvent:
    event_type: EventType
    data: dict
    round: int

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "round": self.round,
        }
