import random

from backend.actions import Action
from backend.entities import Enemy, Player, Position
from backend.config import LevelConfig, PLAYER_MAX_HP, PLAYER_DEFENSE
from backend.entities import Player, Position
from backend.events import GameEvent, EventType


class WorldState:
    def __init__(self, config: LevelConfig, seed: int):
        self.config = config
        self.rng = random.Random(seed)
        self.round = 0
        self.players: dict[str, Player] = {}
        self.enemies: dict[str, Enemy] = {}
        self.walls: set[tuple[int, int]] = set(config.walls)
        self.pending_actions: dict[str, Action] = {}
        self._next_player_num = 1
        self._next_enemy_num = 1

        self.grid: list[list[str | None]] = [
            [None for _ in range(config.width)]
            for _ in range(config.height)
        ]

        for enemy_def in config.enemies:
            pos = enemy_def.position
            self.add_enemy(
                name=enemy_def.name,
                position=Position(pos[0], pos[1]),
                hp=enemy_def.hp,
                attack_damage=enemy_def.attack_damage,
                defense=enemy_def.defense,
            )

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
            defense=PLAYER_DEFENSE,
        )

        self.players[player_id] = player
        self.grid[position.y][position.x] = player_id
        return player

    def add_enemy(self, name: str, position: Position, hp: int, attack_damage: int, defense: int) -> Enemy:
        enemy_id = f"enemy_{self._next_enemy_num}"
        self._next_enemy_num += 1

        enemy = Enemy(
            id=enemy_id,
            name=name,
            position=position,
            hp=hp,
            max_hp=hp,
            attack_damage=attack_damage,
            defense=defense,
        )

        self.enemies[enemy_id] = enemy
        self.grid[position.y][position.x] = enemy_id
        return enemy

    def remove_player(self, player_id: str):
        player = self.players.get(player_id)
        if not player:
            return
        self.grid[player.position.y][player.position.x] = None
        self.pending_actions.pop(player_id, None)
        del self.players[player_id]

    def get_player(self, player_id: str) -> Player | None:
        return self.players.get(player_id)

    def get_entity(self, entity_id: str) -> Player | Enemy | None:
        if entity_id.startswith("player_"):
            return self.players.get(entity_id)
        elif entity_id.startswith("enemy_"):
            return self.enemies.get(entity_id)
        return None

    def is_valid_position(self, x: int, y: int) -> bool:
        if x < 0 or x >= self.config.width or y < 0 or y >= self.config.height:
            return False
        return (x, y) not in self.walls

    def is_occupied(self, x: int, y: int) -> str | None:
        return self.grid[y][x]

    def move_entity(self, entity_id: str, new_pos: Position):
        entity = self.get_entity(entity_id)
        if not entity:
            return
        self.grid[entity.position.y][entity.position.x] = None
        entity.position = new_pos
        self.grid[new_pos.y][new_pos.x] = entity_id

    def living_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_alive]

    def living_enemies(self) -> list[Enemy]:
        return [e for e in self.enemies.values() if e.is_alive]

    def players_pending(self) -> list[str]:
        living_ids = {p.id for p in self.living_players()}
        submitted_ids = set(self.pending_actions.keys())
        return sorted(living_ids - submitted_ids)

    def to_dict(self) -> dict:
        return {
            "round": self.round,
            "grid": self.grid,
            "walls": list(self.walls),
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
            "enemies": {eid: e.to_dict() for eid, e in self.enemies.items()},
            "pending_player_ids": self.players_pending(),
        }
