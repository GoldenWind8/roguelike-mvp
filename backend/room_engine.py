from backend.actions import Action, ActionType
from backend.config import RNG_SEED
from backend.entities import Player, Position
from backend.events import GameEvent, EventType
from backend.room_loader import RoomTemplate
from backend.modes import MODES
from backend.systems import resolve_round
from backend.room_state import RoomState


class RoomEngine:
    def __init__(self, template: RoomTemplate, seed: int = RNG_SEED):
        self.room = RoomState(template, seed)
        self.mode = MODES[template.mode]
        self.started = False
        self.phase = "waiting"

    @property
    def turn_based(self) -> bool:
        """Does this room buffer actions into rounds? (Round timers and
        waiting_for broadcasts only make sense when this is True.)"""
        return self.mode.turn_based

    def join(self, player_name: str) -> tuple[Player, list[GameEvent]]:
        # Room capacity = number of spawn points (replaces the old MAX_PLAYERS).
        if len(self.room.players) >= self.room.template.capacity:
            raise ValueError("Room is full")

        player = self.room.add_player(player_name)

        events = [GameEvent(
            EventType.PLAYER_JOINED,
            {"player_id": player.id, "name": player.name, "position": [player.position.x, player.position.y]},
            self.room.round,
        )]

        if len(self.room.players) >= 1 and not self.started:
            self.started = True
            self.phase = "player_phase"
            events.append(GameEvent(
                EventType.ROUND_STARTED,
                {"round": self.room.round},
                self.room.round,
            ))

        return player, events

    def attach_player(self, player: Player) -> list[GameEvent]:
        """Traversal arrival: place an EXISTING player (hp/id/name preserved)
        at a free spawn. Raises ValueError when the room can't take them —
        the caller denies the traversal and the player stays where they were."""
        if len(self.room.players) >= self.room.template.capacity:
            raise ValueError("The way is blocked")
        spawn = self.room.free_spawn()
        if spawn is None:
            raise ValueError("The way is blocked")

        self.room.attach_player(player, Position(spawn[0], spawn[1]))

        events = [GameEvent(
            EventType.PLAYER_JOINED,
            {"player_id": player.id, "name": player.name, "position": [player.position.x, player.position.y]},
            self.room.round,
        )]

        # Same start logic as join(): arriving in a dormant room wakes it.
        if not self.started:
            self.started = True
            self.phase = "player_phase"
            events.append(GameEvent(
                EventType.ROUND_STARTED,
                {"round": self.room.round},
                self.room.round,
            ))

        return events

    def detach_player(self, player_id: str) -> Player | None:
        """Traversal departure. No PLAYER_LEFT event (the resolution's
        PLAYER_ENTERED_DOOR already tells the story) and no auto-resolve —
        traversal happens after a round resolves, so nobody is pending on us."""
        player = self.room.detach_player(player_id)
        if player is None:
            return None
        if not self.room.living_players():
            self.started = False
            self.phase = "waiting"
        return player

    def submit_action(self, player_id: str, action_data: dict) -> tuple[list[GameEvent], bool]:
        """Returns (events, resolved). If resolved is True, broadcast state to
        all. The room's mode owns the timing: combat buffers into rounds,
        exploration applies valid actions immediately (backend/modes.py)."""

        if self.phase != "player_phase":
            return ([GameEvent(
                EventType.INVALID_ACTION,
                {"reason": "Not accepting actions right now"},
                self.room.round,
            )], False)

        return self.mode.submit(self, player_id, action_data)

    def force_resolve(self) -> list[GameEvent]:
        """Called by timeout — auto-wait for anyone who hasn't acted."""
        for pid in self.room.players_pending():
            self.room.pending_actions[pid] = Action(
                action_type=ActionType.WAIT,
                player_id=pid,
            )
        events, _ = self._resolve_current_round()
        return events

    def _resolve_current_round(self) -> tuple[list[GameEvent], bool]:
        self.phase = "resolving"
        actions = dict(self.room.pending_actions)
        self.room.pending_actions.clear()
        events = resolve_round(self.room, actions)
        self.phase = "player_phase"
        return (events, True)

    def get_state(self) -> dict:
        state = self.room.to_dict()
        state["started"] = self.started
        state["phase"] = self.phase
        return state

    def remove_player(self, player_id: str) -> tuple[list[GameEvent], bool]:
        player = self.room.get_player(player_id)
        if not player:
            return ([], False)

        events = [GameEvent(
            EventType.PLAYER_LEFT,
            {"player_id": player_id, "name": player.name},
            self.room.round,
        )]

        self.room.remove_player(player_id)

        living = self.room.living_players()
        if self.started and len(living) == 0:
            self.started = False
            self.phase = "waiting"
        elif self.started and not self.room.players_pending():
            resolve_events, _ = self._resolve_current_round()
            events.extend(resolve_events)
            return (events, True)

        return (events, False)
