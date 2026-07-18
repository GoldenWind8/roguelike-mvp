"""spawn_loot — the ONE entry point for "the world gives a player an item"
(docs/LOOT.md). Chests call it today; NPC gifts, boss drops, and enemy
on_death hooks call this same function tomorrow with their own weights.

The decision stack, per roll:
  1. rarity  — weighted roll over `weights` (DATA, defaulting to
     config.LOOT_WEIGHTS 60/35/5; callers pass their own table, never flags —
     a boss chest is spawn_loot(weights=BOSS_WEIGHTS), not a boolean).
  2. source  — a small chance (config.LOOT_LLM_CHANCE) the premium LLM mints
     a NEVER-BEFORE-SEEN item of that rarity at open time (the suspense is a
     feature); it is validated, clamped to the rarity band, and inserted into
     the pool so the game's item universe grows permanently. ANY failure —
     tier unconfigured, timeout, malformed JSON, validation reject — falls
     back silently to a pool draw: a chest must never break because an API
     is down, and the player can't tell a fallback from a normal find.

Async on purpose: it reads the DB and maybe an LLM, so it runs at the edges
(main.py's open-chest request) and never inside round resolution.
"""
import logging
import random

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import CHEST_ITEM_COUNT_WEIGHTS, LOOT_LLM_CHANCE, LOOT_WEIGHTS
from backend.item_gen import generate_item
from backend.item_store import draw_random
from backend.llm import tier_available

logger = logging.getLogger(__name__)


def roll_rarity(weights: dict[str, int] | None = None,
                rng: random.Random | None = None) -> str:
    weights = weights or LOOT_WEIGHTS
    chooser = rng if rng is not None else random
    rarities = list(weights)
    return chooser.choices(rarities, weights=[weights[r] for r in rarities], k=1)[0]


def roll_item_count(weights: dict[int, int] | None = None,
                    rng: random.Random | None = None) -> int:
    """How many items one chest holds (default 1-3, weighted toward 1 —
    config.CHEST_ITEM_COUNT_WEIGHTS). A weights table like roll_rarity's,
    for the same reason: a vault chest passes its own, never a flag."""
    weights = weights or CHEST_ITEM_COUNT_WEIGHTS
    chooser = rng if rng is not None else random
    counts = list(weights)
    return chooser.choices(counts, weights=[weights[c] for c in counts], k=1)[0]


async def spawn_loot(session: AsyncSession, *,
                     weights: dict[str, int] | None = None,
                     rng: random.Random | None = None) -> tuple[dict | None, bool]:
    """Roll one item. Returns (item_view, freshly_minted) — freshly_minted is
    True only when the LLM invented it just now (the client gives that
    fanfare). (None, False) only if the pool is truly empty."""
    chooser = rng if rng is not None else random
    rarity = roll_rarity(weights, rng)

    if chooser.random() < LOOT_LLM_CHANCE and tier_available("premium"):
        try:
            item = await generate_item(session, rarity)
            return item, True
        except Exception as e:
            # Deliberately broad: whatever went wrong (transport, JSON, the
            # gate), the remedy is identical — draw the pool instead.
            logger.warning("LLM item generation fell back to pool draw: %s", e)

    return await draw_random(session, rarity, rng if rng is not None else None), False
