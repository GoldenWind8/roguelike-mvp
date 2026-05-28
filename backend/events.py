from dataclasses import dataclass
from enum import Enum


class EventType(Enum):
    PLAYER_JOINED = "player_joined"
    PLAYER_MOVED = "player_moved"
    PLAYER_ATTACKED = "player_attacked"
    PLAYER_DAMAGED = "player_damaged"
    PLAYER_DIED = "player_died"
    ENEMY_MOVED = "enemy_moved"
    ENEMY_ATTACKED = "enemy_attacked"
    ENEMY_DIED = "enemy_died"
    ROUND_STARTED = "round_started"
    INVALID_ACTION = "invalid_action"
    PLAYER_LEFT = "player_left"
    GAME_OVER = "game_over"


@dataclass
class GameEvent:
    event_type: EventType
    data: dict
    tick: int

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "tick": self.tick,
        }
