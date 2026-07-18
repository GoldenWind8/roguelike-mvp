"""Items at rest — the global item pool's DB access (docs/LOOT.md).

The items twin of npc_store/player_store: everything that reads or writes the
`items` table lives here, and it is only ever called at the async edges
(startup seeding, a chest opening) — never inside round resolution.
"""
import random

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.items import item_view, validate_item
from backend.models import ItemDef


async def insert_item(session: AsyncSession, data: dict, *, origin: str) -> dict:
    """Validate-then-store (the room_validation rhythm) and return the minted
    item's view dict. Every insert path — seed or LLM — passes the same gate;
    a ValueError here means the caller's data was bad, and nothing was written."""
    validate_item(data)
    row = ItemDef(
        name=data["name"].strip(),
        description=data["description"].strip(),
        rarity=data["rarity"],
        item_type=data["type"],
        art=data["art"],
        payload=data["payload"],
        origin=origin,
    )
    session.add(row)
    await session.flush()  # populate row.id; the caller owns the commit
    return item_view(row)


async def draw_random(session: AsyncSession, rarity: str, rng: random.Random | None = None) -> dict | None:
    """A uniformly random item of `rarity` from the pool, as a view dict —
    or None only if the pool is empty even after falling back to ANY rarity
    (a freshly seeded world can't hit that; it guards a hand-emptied table).

    Drawn in Python, not ORDER BY RANDOM(): the pool is small, and taking a
    seeded rng keeps loot tests deterministic like combat (config.RNG_SEED)."""
    rows = (await session.execute(
        select(ItemDef).where(ItemDef.rarity == rarity)
    )).scalars().all()
    if not rows:
        rows = (await session.execute(select(ItemDef))).scalars().all()
    if not rows:
        return None
    chooser = rng if rng is not None else random
    return item_view(chooser.choice(rows))


async def pool_is_empty(session: AsyncSession) -> bool:
    count = (await session.execute(select(func.count(ItemDef.id)))).scalar_one()
    return count == 0
