from dataclasses import dataclass
from enum import Enum


class ActionType(Enum):
    MOVE = "move"
    ATTACK = "attack"
    WAIT = "wait"


@dataclass
class Action:
    action_type: ActionType
    player_id: str
    direction: tuple[int, int] | None = None
    target_id: str | None = None


def parse_action(player_id: str, data: dict) -> Action:
    action_type = ActionType(data["action_type"])
    direction = tuple(data["direction"]) if "direction" in data else None
    target_id = data.get("target_id")
    return Action(
        action_type=action_type,
        player_id=player_id,
        direction=direction,
        target_id=target_id,
    )
