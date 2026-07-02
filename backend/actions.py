from dataclasses import dataclass
from enum import Enum


class ActionType(Enum):
    MOVE = "move"
    ATTACK = "attack"
    WAIT = "wait"
    BOMB = "bomb"


@dataclass
class Action:
    action_type: ActionType
    player_id: str
    direction: tuple[int, int] | None = None
    target_id: str | None = None
    target_tile: tuple[int, int] | None = None


def _coord_pair(value) -> tuple[int, int] | None:
    # Client payloads are untrusted: anything that isn't a 2-item [x, y] list
    # becomes None so handler validation rejects the action cleanly instead of
    # an unpack blowing up mid-parse.
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (value[0], value[1])
    return None


def parse_action(player_id: str, data: dict) -> Action:
    action_type = ActionType(data["action_type"])
    direction = _coord_pair(data.get("direction"))
    target_id = data.get("target_id")
    target_tile = _coord_pair(data.get("target_tile"))
    return Action(
        action_type=action_type,
        player_id=player_id,
        direction=direction,
        target_id=target_id,
        target_tile=target_tile
    )
