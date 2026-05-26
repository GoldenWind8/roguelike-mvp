import random

from backend.config import LevelConfig, PLAYER_MAX_HP
from backend.entities import Player, Position
from backend.events import GameEvent, EventType


class WorldState:
    def __init__(self, config: LevelConfig, seed: int):
        self.config = config
        self.rng = random.Random(seed)
        self.tick = 0
        self.players: dict[str, Player] = {}
        self.walls: set[tuple[int, int]] = set(config.walls)
        self.turn_order: list[str] = []
        self.current_turn_index = 0
        self._next_player_num = 1

        self.grid: list[list[str | None]] = [
            [None for _ in range(config.width)]
            for _ in range(config.height)
        ]

    def add_player(self, name: str) -> Player:
        player_id = f"player_{self._next_player_num}"
        self._next_player_num += 1

        spawn = self.config.spawn_points[len(self.players)]
        position = Position(spawn[0], spawn[1])

        player = Player(
            id=player_id,
            name=name,
            position=position,
            hp=PLAYER_MAX_HP,
            max_hp=PLAYER_MAX_HP,
        )

        self.players[player_id] = player
        self.grid[position.y][position.x] = player_id
        self.turn_order.append(player_id)
        return player

    def remove_player(self, player_id: str):
        player = self.players.get(player_id)
        if not player:
            return
        self.grid[player.position.y][player.position.x] = None
        if player_id in self.turn_order:
            idx = self.turn_order.index(player_id)
            self.turn_order.remove(player_id)
            if idx < self.current_turn_index:
                self.current_turn_index -= 1
            if self.turn_order and self.current_turn_index >= len(self.turn_order):
                self.current_turn_index = 0
        del self.players[player_id]

    def get_player(self, player_id: str) -> Player | None:
        return self.players.get(player_id)

    def is_valid_position(self, x: int, y: int) -> bool:
        if x < 0 or x >= self.config.width or y < 0 or y >= self.config.height:
            return False
        return (x, y) not in self.walls

    def is_occupied(self, x: int, y: int) -> str | None:
        return self.grid[y][x]

    def move_entity(self, player_id: str, new_pos: Position):
        player = self.players[player_id]
        self.grid[player.position.y][player.position.x] = None
        player.position = new_pos
        self.grid[new_pos.y][new_pos.x] = player_id

    def current_player_id(self) -> str | None:
        if not self.turn_order:
            return None
        return self.turn_order[self.current_turn_index]

    def advance_turn(self):
        if not self.turn_order:
            return
        start = self.current_turn_index
        while True:
            self.current_turn_index = (self.current_turn_index + 1) % len(self.turn_order)
            pid = self.turn_order[self.current_turn_index]
            player = self.players.get(pid)
            if player and player.is_alive:
                break
            if self.current_turn_index == start:
                break
        self.tick += 1

    def living_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_alive]

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "current_turn": self.current_player_id(),
            "grid": self.grid,
            "walls": list(self.walls),
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
        }
