from dataclasses import dataclass, field


GRID_WIDTH = 10
GRID_HEIGHT = 10
PLAYER_MAX_HP = 10
PLAYER_ATTACK_DAMAGE = 2
PLAYER_DEFENSE = 1
MAX_PLAYERS = 4
RNG_SEED = 42
TURN_TIMEOUT = 30
ENEMY_CHASE_RANGE = 5


@dataclass
class EnemyDef:
    name: str
    hp: int
    attack_damage: int
    position: tuple[int, int]

@dataclass
class LevelConfig:
    width: int
    height: int
    spawn_points: list[tuple[int, int]]
    walls: list[tuple[int, int]]
    enemies: list[EnemyDef] = field(default_factory=list)


DEFAULT_LEVEL = LevelConfig(
    width=GRID_WIDTH,
    height=GRID_HEIGHT,
    spawn_points=[(0, 0), (9, 9), (0, 9), (9, 0)],
    walls=[(4, 4), (4, 5), (5, 4), (5, 5)],
    enemies=[
        EnemyDef(
            name="Goblin",
            hp=6,
            attack_damage=1,
            position=(3, 3),
        ),
        EnemyDef(
            name="Skeleton",
            hp=8,
            attack_damage=2,
            position=(6, 6),
        ),
        EnemyDef(
            name="Rat",
            hp=4,
            attack_damage=3,
            position=(7, 6),
        ),
        EnemyDef(
            name="Angry bunny",
            hp=7,
            attack_damage=0,
            position=(5, 3),
        )

    ],
)
