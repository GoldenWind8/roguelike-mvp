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


# Session-token signing secret (ACCOUNTS.md Decision 5): a token is
# HMAC(player_id, SECRET_KEY), so whoever holds this secret can mint a session
# for any account — prod MUST set a real value in the environment. The dev
# default is deliberately stable (not random-per-boot) so local tokens survive
# a server restart instead of logging everyone out on every code reload.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-not-for-production")


# Session-token signing secret (ACCOUNTS.md Decision 5). A token is
# HMAC(player_id, SECRET_KEY) — anyone holding the secret can mint a session
# for any account, so a real deployment MUST set this in the environment.
# The dev default is deliberately stable (not random-per-boot) so local
# tokens survive server restarts; a rotating secret would log everyone out
# on every reload.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-not-for-production")

# Player defaults.
PLAYER_MAX_HP = 100
PLAYER_ATTACK_DAMAGE = 30
PLAYER_DEFENSE = 1

# Engine tuning. RNG_SEED drives combat determinism (not level content — levels
# live in the DB now). Room capacity comes from the room's spawn-point count.
RNG_SEED = 42
TURN_TIMEOUT = 30
ENEMY_CHASE_RANGE = 5


# Loot system (docs/LOOT.md).
INVENTORY_SLOTS = 10
# Rarity weights for the DEFAULT spawn_loot roll — a DATA table, not
# constants, because callers may pass their own (a boss chest wants better
# odds) and depth-scaling will want to derive variants of it.
LOOT_WEIGHTS = {"common": 60, "rare": 35, "legendary": 5}
# Chance a chest mints a brand-new LLM item instead of drawing the pool —
# only taken when the premium tier is configured; failures silently fall
# back to a pool draw, so this can never break a chest.
LOOT_LLM_CHANCE = float(os.getenv("LOOT_LLM_CHANCE", "0.10"))
# Generous on purpose, unlike DIALOGUE_TIMEOUT's hard 8s: a chest-open is a
# rare (LOOT_LLM_CHANCE) moment where waiting IS the suspense, and premium
# bindings are often reasoning models (gemini-pro measured >8s). On timeout
# the player silently gets a pool draw instead — never a broken chest.
LOOT_LLM_TIMEOUT = float(os.getenv("LOOT_LLM_TIMEOUT", "20.0"))
# Reasoning models spend hidden thinking tokens from this same budget (see
# DIALOGUE_MAX_TOKENS) — too small truncates the JSON mid-payload, which
# reads as "the LLM wrote garbage" but is really "we cut it off". Measured
# on gemini-pro: ~1.1-1.3k thinking + ~200 of item JSON, with high variance;
# 4096 leaves the long-thinking rolls room. The item is small — the budget
# is almost entirely for thoughts we never see.
LOOT_LLM_MAX_TOKENS = int(os.getenv("LOOT_LLM_MAX_TOKENS", "4096"))
# How often the world-clock ticker sweeps rooms for expired timed effects.
# Coarse on purpose: expiry is ALSO checked lazily at every stat read, so
# this only bounds how long a stale buff can linger on screen.
WORLD_TICK_INTERVAL = 2.0

# How many items a chest rolls at open time — a weights table like
# LOOT_WEIGHTS, and data for the same reason: a vault chest passes its own.
CHEST_ITEM_COUNT_WEIGHTS = {1: 60, 2: 30, 3: 10}

# Hunger (docs/LOOT.md Decision 5): a 0-100 meter on players, drained by the
# world ticker ONLY while its owner is connected and alive — offline time
# costs nothing (logging in starved would punish having a life; Minecraft
# pauses, we do the equivalent). Full-to-empty in ~15 minutes of play.
HUNGER_MAX = 100
HUNGER_DRAIN_PER_S = 100 / 900
# Well fed (the Minecraft rule): at/above this the meter slowly knits wounds —
# 1 hp per tick, each hp costing extra hunger on top of the base drain.
HUNGER_REGEN_THRESHOLD = 80
HUNGER_REGEN_COST = 0.4
# Starving (the Don't Starve rule): at 0 the meter eats you instead — base
# damage per tick, routed through the normal Damage effect (its min-1 clamp
# means armor cannot make starvation free). It CAN kill you.
HUNGER_STARVE_DAMAGE = 1


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
# Party-size cap (NPCS.md "Followers"): most followers one player may hold.
# Counted per-owner among the NPCs currently loaded in the room the recruit
# happens in. The players table (M8) makes a global owner-centric cap query
# possible — deferred until a second recruitable NPC makes it reachable
# (ACCOUNTS.md "Deferred").
PARTY_SIZE_CAP = 3
# Follower leash: a follower only closes the gap to its owner when farther than
# this (Manhattan). Keeps an idle ally a step back instead of glued to your
# tile — the *tuning* half of the ally-blocking fix (the swap rule is the other).
FOLLOW_LEASH = 2
# Player text is untrusted input; cap it before it reaches a prompt.
TALK_TEXT_LIMIT = 300

# DEV-only affordances (e.g. the "reseed world" button). Destructive — it boots
# every connected player and wipes individual state — so it is gated behind this
# flag and OFF unless explicitly enabled. Never enable in a shared deployment.
DEV_MODE = os.getenv("DEV_MODE", "1") not in ("0", "false", "False", "")
