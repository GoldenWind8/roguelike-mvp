from dataclasses import dataclass


GRID_WIDTH = 10
GRID_HEIGHT = 10
PLAYER_MAX_HP = 10
PLAYER_ATTACK_DAMAGE = 2
MAX_PLAYERS = 4
RNG_SEED = 42


@dataclass
class LevelConfig:
    width: int
    height: int
    spawn_points: list[tuple[int, int]]
    walls: list[tuple[int, int]]


DEFAULT_LEVEL = LevelConfig(
    width=GRID_WIDTH,
    height=GRID_HEIGHT,
    spawn_points=[(0, 0), (9, 9), (0, 9), (9, 0)],
    walls=[(4, 4), (4, 5), (5, 4), (5, 5)],
)
