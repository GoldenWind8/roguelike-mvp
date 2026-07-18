from dataclasses import dataclass
from enum import Enum


class ActionType(Enum):
    MOVE = "move"
    ATTACK = "attack"
    WAIT = "wait"
    # Spend an inventory item (docs/LOOT.md): CONSUME drinks/eats slot N on
    # yourself; THROW arcs slot N at a target tile. The old hard-coded "bomb"
    # action became a common throwable ITEM — same numbers, now data.
    CONSUME = "consume"
    THROW = "throw"


@dataclass
class Action:
    action_type: ActionType
    player_id: str
    direction: tuple[int, int] | None = None
    target_id: str | None = None
    target_tile: tuple[int, int] | None = None
    # Which inventory slot a consume/throw spends. Validated by the handler
    # (existence, right item type) like every other untrusted field.
    slot: int | None = None


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
    slot = data.get("slot")
    return Action(
        action_type=action_type,
        player_id=player_id,
        direction=direction,
        target_id=target_id,
        target_tile=target_tile,
        slot=slot if isinstance(slot, int) else None,
    )
