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


def _art_value(row: ItemDef) -> str | None:
    art = row.art
    return art.get("value") if isinstance(art, dict) else None


async def draw_with_preference(
    session: AsyncSession,
    rarity: str,
    *,
    preferred_art_values: frozenset[str] | set[str] = frozenset(),
    excluded_art_values: frozenset[str] | set[str] = frozenset(),
    preference: float = 0.0,
    rng: random.Random | None = None,
) -> dict | None:
    """Draw from the pool with a bounded preference for authored content.

    Exclusion happens before the normal "requested rarity, then any rarity"
    fallback.  When both preferred and ordinary candidates exist, ``preference``
    is the probability of choosing the preferred bucket; the final pick within
    either bucket remains uniform.  An empty bucket always falls back to the
    other one, so regional content can never make a valid chest fail.

    Art values are used as immutable content markers.  This keeps the generic
    item schema untouched and leaves unrelated seed/LLM rows completely
    eligible and unmodified.
    """
    if not 0.0 <= preference <= 1.0:
        raise ValueError("preference must be between 0 and 1")

    all_rows = (await session.execute(select(ItemDef))).scalars().all()
    eligible = [
        row for row in all_rows
        if _art_value(row) not in excluded_art_values
    ]
    candidates = [row for row in eligible if row.rarity == rarity] or eligible
    if not candidates:
        return None

    preferred = [
        row for row in candidates
        if _art_value(row) in preferred_art_values
    ]
    ordinary = [
        row for row in candidates
        if _art_value(row) not in preferred_art_values
    ]
    chooser = rng if rng is not None else random
    if preferred and ordinary:
        bucket = preferred if chooser.random() < preference else ordinary
    else:
        bucket = preferred or ordinary
    return item_view(chooser.choice(bucket))


async def insert_missing_authored_items(
    session: AsyncSession,
    definitions,
    *,
    origin: str = "seed",
) -> int:
    """Insert immutable authored definitions absent from an existing pool.

    A bundled URL art value is the stable marker.  Existing rows are never
    edited, starter items are not replayed, and a second call is a no-op.
    The caller owns the commit, matching :func:`insert_item`.
    """
    rows = (await session.execute(select(ItemDef))).scalars().all()
    existing_markers = {
        _art_value(row)
        for row in rows
        if _art_value(row) is not None
    }
    inserted = 0
    for definition in definitions:
        art = definition.get("art")
        marker = art.get("value") if isinstance(art, dict) else None
        if not isinstance(marker, str) or not marker:
            raise ValueError("authored item backfill requires a stable art.value")
        if marker in existing_markers:
            continue
        await insert_item(session, definition, origin=origin)
        existing_markers.add(marker)
        inserted += 1
    return inserted


async def pool_is_empty(session: AsyncSession) -> bool:
    count = (await session.execute(select(func.count(ItemDef.id)))).scalar_one()
    return count == 0
