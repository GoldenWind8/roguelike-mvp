from backend.actions import Action, ActionType, parse_action
from backend.config import RNG_SEED
from backend.entities import Player, Position
from backend.events import GameEvent, EventType
from backend.level_loader import LevelData
from backend.systems import resolve_round, validate_player_action
from backend.world import WorldState


class Game:
    def __init__(self, level: LevelData, seed: int = RNG_SEED):
        self.world = WorldState(level, seed)
        self.started = False
        self.phase = "waiting"

    def join(self, player_name: str) -> tuple[Player, list[GameEvent]]:
        # Room capacity = number of spawn points (replaces the old MAX_PLAYERS).
        if len(self.world.players) >= self.world.config.capacity:
            raise ValueError("Game is full")

        player = self.world.add_player(player_name)

        events = [GameEvent(
            EventType.PLAYER_JOINED,
            {"player_id": player.id, "name": player.name, "position": [player.position.x, player.position.y]},
            self.world.round,
        )]

        if len(self.world.players) >= 1 and not self.started:
            self.started = True
            self.phase = "player_phase"
            events.append(GameEvent(
                EventType.ROUND_STARTED,
                {"round": self.world.round},
                self.world.round,
            ))

        return player, events

    def attach_player(self, player: Player) -> list[GameEvent]:
        """Traversal arrival: place an EXISTING player (hp/id/name preserved)
        at a free spawn. Raises ValueError when the room can't take them —
        the caller denies the traversal and the player stays where they were."""
        if len(self.world.players) >= self.world.config.capacity:
            raise ValueError("The way is blocked")
        spawn = self.world.free_spawn()
        if spawn is None:
            raise ValueError("The way is blocked")

        self.world.attach_player(player, Position(spawn[0], spawn[1]))

        events = [GameEvent(
            EventType.PLAYER_JOINED,
            {"player_id": player.id, "name": player.name, "position": [player.position.x, player.position.y]},
            self.world.round,
        )]

        # Same start logic as join(): arriving in a dormant room wakes it.
        if not self.started:
            self.started = True
            self.phase = "player_phase"
            events.append(GameEvent(
                EventType.ROUND_STARTED,
                {"round": self.world.round},
                self.world.round,
            ))

        return events

    def detach_player(self, player_id: str) -> Player | None:
        """Traversal departure. No PLAYER_LEFT event (the resolution's
        PLAYER_ENTERED_DOOR already tells the story) and no auto-resolve —
        traversal happens after a round resolves, so nobody is pending on us."""
        player = self.world.detach_player(player_id)
        if player is None:
            return None
        if not self.world.living_players():
            self.started = False
            self.phase = "waiting"
        return player

    def submit_action(self, player_id: str, action_data: dict) -> tuple[list[GameEvent], bool]:
        """Returns (events, round_resolved). If round_resolved is True, broadcast state to all."""

        if self.phase != "player_phase":
            return ([GameEvent(
                EventType.INVALID_ACTION,
                {"reason": "Not accepting actions right now"},
                self.world.round,
            )], False)

        if player_id in self.world.pending_actions:
            return ([GameEvent(
                EventType.INVALID_ACTION,
                {"reason": "You already submitted an action this round"},
                self.world.round,
            )], False)

        action = parse_action(player_id, action_data)
        error = validate_player_action(self.world, action)
        if error:
            return ([error], False)

        self.world.pending_actions[player_id] = action

        if not self.world.players_pending():
            return self._resolve_current_round()

        return ([], False)

    def force_resolve(self) -> list[GameEvent]:
        """Called by timeout — auto-wait for anyone who hasn't acted."""
        for pid in self.world.players_pending():
            self.world.pending_actions[pid] = Action(
                action_type=ActionType.WAIT,
                player_id=pid,
            )
        events, _ = self._resolve_current_round()
        return events

    def _resolve_current_round(self) -> tuple[list[GameEvent], bool]:
        self.phase = "resolving"
        actions = dict(self.world.pending_actions)
        self.world.pending_actions.clear()
        events = resolve_round(self.world, actions)
        self.phase = "player_phase"
        return (events, True)

    def get_state(self) -> dict:
        state = self.world.to_dict()
        state["started"] = self.started
        state["phase"] = self.phase
        return state

    def remove_player(self, player_id: str) -> tuple[list[GameEvent], bool]:
        player = self.world.get_player(player_id)
        if not player:
            return ([], False)

        events = [GameEvent(
            EventType.PLAYER_LEFT,
            {"player_id": player_id, "name": player.name},
            self.world.round,
        )]

        self.world.remove_player(player_id)

        living = self.world.living_players()
        if self.started and len(living) == 0:
            self.started = False
            self.phase = "waiting"
        elif self.started and not self.world.players_pending():
            resolve_events, _ = self._resolve_current_round()
            events.extend(resolve_events)
            return (events, True)

        return (events, False)
