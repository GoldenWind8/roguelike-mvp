"""Room timing modes — the `RoomMode` seam from docs/WORLD.md.

One grid model, two timing models: a mode decides WHEN a submitted action
resolves, never HOW. Both modes validate and resolve through the same
HANDLERS, so movement rules, door traversal, effects, and events stay one
rules engine (the WORLD.md tripwire: if a mode needs special cases inside
handlers, the boundary is wrong).

  - TurnBasedMode   buffer actions, resolve when all players submit (combat)
  - ExplorationMode validate and resolve immediately (walking, examining)

WHICH mode a room is in is not stored anywhere — it is derived, live, by
`derive_mode` (ROADMAP M7): combat iff a living hostile is present. The old
load-time inference ("a room with enemies is a combat room") was this same
predicate evaluated once; now the engine re-evaluates it at every resolution
point, so an insulted caretaker escalates his room and a cleared room calms
down, with no static mode to go stale.
"""
from abc import ABC, abstractmethod
from itertools import chain

from backend.actions import ActionType, parse_action
from backend.entities import Disposition
from backend.events import GameEvent, EventType
from backend.handlers import HANDLERS
from backend.room_state import RoomState
from backend.systems import validate_player_action


def derive_mode(room: RoomState) -> str:
    """The M7 predicate: "combat" iff a living actor hostile to the players is
    present, "exploration" otherwise. One function, so every caller (engine
    construction, round end, dialogue effects) agrees on what mode means.

    Cost: O(actors in the room) with an early exit on the first hostile, no
    allocation beyond the chain iterator — and it only runs at resolution
    points (a round resolving, a dialogue effect landing, a room loading),
    never per websocket message, so it cannot become a hot-loop drag. Players
    are skipped: they are never hostile to themselves."""
    for actor in chain(room.enemies.values(), room.npcs.values()):
        if actor.is_alive and actor.disposition is Disposition.HOSTILE:
            return "combat"
    return "exploration"


class RoomMode(ABC):
    """Decides how a room accepts and resolves player actions."""

    # True when the room buffers actions into rounds (timeouts, waiting_for
    # banners, pending players). Exploration has none of that machinery.
    turn_based: bool

    @abstractmethod
    def submit(self, engine, player_id: str, action_data: dict) -> tuple[list[GameEvent], bool]:
        """Accept player intent. Returns (events, resolved) — when resolved is
        True the caller broadcasts the new state to the whole room."""


class TurnBasedMode(RoomMode):
    """The existing combat loop: collect one action per living player, then
    resolve the round (moves, attacks, enemy phase) all at once."""

    turn_based = True

    def submit(self, engine, player_id: str, action_data: dict) -> tuple[list[GameEvent], bool]:
        room = engine.room

        if player_id in room.pending_actions:
            return ([GameEvent(
                EventType.INVALID_ACTION,
                {"reason": "You already submitted an action this round"},
                room.round,
            )], False)

        action = parse_action(player_id, action_data)
        error = validate_player_action(room, action)
        if error:
            return ([error], False)

        room.pending_actions[player_id] = action

        if not room.players_pending():
            return engine._resolve_current_round()

        return ([], False)


class ExplorationMode(RoomMode):
    """Immediate resolution: a valid action applies the moment it arrives —
    nobody waits for anybody. No rounds, no enemy phase, no game-over checks
    (exploration rooms have nothing to fight)."""

    turn_based = False

    ALLOWED = {ActionType.MOVE}

    def submit(self, engine, player_id: str, action_data: dict) -> tuple[list[GameEvent], bool]:
        room = engine.room

        action = parse_action(player_id, action_data)
        if action.action_type not in self.ALLOWED:
            return ([GameEvent(
                EventType.INVALID_ACTION,
                {"reason": "There is nothing to fight here"},
                room.round,
            )], False)

        # Same authoritative validation combat uses — the server owns movement
        # rules in both modes; only the timing differs.
        error = validate_player_action(room, action)
        if error:
            return ([error], False)

        events = HANDLERS[action.action_type].resolve(room, action)
        return (events, True)


# Mode instances are stateless — one shared instance per mode is enough.
MODES: dict[str, RoomMode] = {
    "combat": TurnBasedMode(),
    "exploration": ExplorationMode(),
}
