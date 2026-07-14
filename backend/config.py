import os
from pathlib import Path


def _load_dotenv() -> None:
    """Fill os.environ from the repo-root .env (real env vars win). No
    python-dotenv dependency for six lines of parsing; same contract as
    tools/generate_assets.py: env first, .env as fallback."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


# Storage config — 12-factor: the driver lives IN the URL, so the same code
# runs on SQLite (tests) and Postgres (prod) by swapping one env var.
#   tests/local: sqlite+aiosqlite:///./game.db
#   prod:        postgresql+asyncpg://user:pass@host/db
# Never hardcode credentials — prod sets DATABASE_URL in the environment.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./game.db")


# Player defaults.
PLAYER_MAX_HP = 100
PLAYER_ATTACK_DAMAGE = 30
PLAYER_DEFENSE = 1

# Engine tuning. RNG_SEED drives combat determinism (not level content — levels
# live in the DB now). Room capacity comes from the room's spawn-point count.
RNG_SEED = 42
TURN_TIMEOUT = 30
ENEMY_CHASE_RANGE = 5


BOMB_DAMAGE = 3
BOMB_RADIUS = 1
BOMB_THROW_RANGE = 4


# NPC dialogue (NPCS.md "LLM Dialogue Source"). Key/model/URL from env, never
# code. No key -> the server runs canned-only; dialogue availability must
# never affect the sim.
GRID_API_KEY = os.getenv("GRID_API_KEY", "")
GRID_BASE_URL = os.getenv("GRID_BASE_URL", "https://api.aipowergrid.io")
DIALOGUE_MODEL = os.getenv("DIALOGUE_MODEL", "auto")
# Hard timeout on every LLM call — timeout/rate-limit/down all degrade to a
# canned line, the request is dropped, never queued.
DIALOGUE_TIMEOUT = float(os.getenv("DIALOGUE_TIMEOUT", "8.0"))
# Completion token budget. The grid routes to REASONING models (e.g.
# gpt-oss-120b) whose hidden reasoning_content shares this budget with the
# visible answer — too small and the model spends it all thinking and returns
# EMPTY content (finish_reason "length"), which then degrades to a canned line
# and never proposes an effect. 512 leaves comfortable room for reasoning + the
# JSON envelope across all grid models (measured); raise it if empty-completion
# warnings recur.
DIALOGUE_MAX_TOKENS = int(os.getenv("DIALOGUE_MAX_TOKENS", "512"))
# Bounded dialogue memory: how many transcript entries an NPC keeps (one
# entry = one speaker line). Persisted with the NPC row.
NPC_TRANSCRIPT_LIMIT = 30
# Player text is untrusted input; cap it before it reaches a prompt.
TALK_TEXT_LIMIT = 300
