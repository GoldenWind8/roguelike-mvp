from backend.actions import parse_action
from backend.config import DEFAULT_LEVEL, MAX_PLAYERS, RNG_SEED, LevelConfig
from backend.entities import Player
from backend.events import GameEvent, EventType
from backend.systems import process_action
from backend.world import WorldState


class Game:
    def __init__(self, level_config: LevelConfig = DEFAULT_LEVEL, seed: int = RNG_SEED):
        self.world = WorldState(level_config, seed)
        self.started = False

    def join(self, player_name: str) -> tuple[Player, list[GameEvent]]:
        if len(self.world.players) >= MAX_PLAYERS:
            raise ValueError("Game is full")

        player = self.world.add_player(player_name)
        events = [GameEvent(
            EventType.PLAYER_JOINED,
            {"player_id": player.id, "name": player.name, "position": [player.position.x, player.position.y]},
            self.world.tick,
        )]

        if len(self.world.players) >= 2 and not self.started:
            self.started = True
            events.append(GameEvent(
                EventType.TURN_STARTED,
                {"player_id": self.world.current_player_id()},
                self.world.tick,
            ))

        return player, events

    def submit_action(self, player_id: str, action_data: dict) -> list[GameEvent]:
        if not self.started:
            return [GameEvent(
                EventType.INVALID_ACTION,
                {"reason": "Game hasn't started yet — waiting for more players"},
                self.world.tick,
            )]
        action = parse_action(player_id, action_data)
        return process_action(self.world, action)

    def get_state(self) -> dict:
        state = self.world.to_dict()
        state["started"] = self.started
        return state

    def remove_player(self, player_id: str) -> list[GameEvent]:
        player = self.world.get_player(player_id)
        if not player:
            return []

        was_current_turn = self.world.current_player_id() == player_id
        events = [GameEvent(
            EventType.PLAYER_LEFT,
            {"player_id": player_id, "name": player.name},
            self.world.tick,
        )]

        self.world.remove_player(player_id)

        if was_current_turn and self.world.turn_order:
            events.append(GameEvent(
                EventType.TURN_STARTED,
                {"player_id": self.world.current_player_id()},
                self.world.tick,
            ))

        living = self.world.living_players()
        if self.started and len(living) == 1:
            events.append(GameEvent(
                EventType.GAME_OVER,
                {"winner_id": living[0].id, "winner_name": living[0].name},
                self.world.tick,
            ))
        elif len(living) == 0:
            self.started = False

        return events
