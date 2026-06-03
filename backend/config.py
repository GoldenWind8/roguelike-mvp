import os


# Storage config — 12-factor: the driver lives IN the URL, so the same code
# runs on SQLite (tests) and Postgres (prod) by swapping one env var.
#   tests/local: sqlite+aiosqlite:///./game.db
#   prod:        postgresql+asyncpg://user:pass@host/db
# Never hardcode credentials — prod sets DATABASE_URL in the environment.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./game.db")


# Player defaults.
PLAYER_MAX_HP = 10
PLAYER_ATTACK_DAMAGE = 3
PLAYER_DEFENSE = 1

# Engine tuning. RNG_SEED drives combat determinism (not level content — levels
# live in the DB now). Room capacity comes from the room's spawn-point count.
RNG_SEED = 42
TURN_TIMEOUT = 30
ENEMY_CHASE_RANGE = 5


BOMB_DAMAGE=3
BOMB_RADIUS=1
BOMB_THROW_RANGE=4