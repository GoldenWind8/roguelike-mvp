"""Persistent, globally shared shop stock and transactional purchases."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import random

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.inventory import inventory_with_item
from backend.loot import spawn_loot
from backend.models import PlayerRow, ShopState, ShopStock
from backend.shop_defs import ShopDefinition


PRICE_RANGES = {
    "common": (5, 12),
    "rare": (18, 35),
    "legendary": (60, 90),
}

# A fence pays enough for exploration finds to fund regional travel, while
# remaining strictly below the cheapest same-rarity shop offer. Buying stock
# merely to sell it back can therefore never mint money.
BUYBACK_PRICES = {
    "common": 4,
    "rare": 12,
    "legendary": 40,
}


class PurchaseError(Exception):
    """A safe, player-facing purchase refusal."""


@dataclass(frozen=True)
class Purchase:
    item: dict
    price: int
    inventory: list
    coins: int
    slot: int


@dataclass(frozen=True)
class Sale:
    item: dict
    price: int
    inventory: list
    coins: int
    slot: int


def utc_day() -> date:
    return datetime.now(timezone.utc).date()


def next_restock_at(day: date) -> str:
    return datetime.combine(day + timedelta(days=1), time(), timezone.utc).isoformat()


def price_for(item: dict, rng: random.Random | None = None) -> int:
    low, high = PRICE_RANGES[item["rarity"]]
    chooser = rng if rng is not None else random
    return chooser.randint(low, high)


def buyback_price_for(item: dict) -> int:
    rarity = item.get("rarity") if isinstance(item, dict) else None
    try:
        return BUYBACK_PRICES[rarity]
    except (KeyError, TypeError):
        raise PurchaseError("The broker cannot price that item.")


def stock_view(row: ShopStock) -> dict:
    return {
        "slot": row.slot,
        "item": row.item,
        "price": row.price,
        "minted": row.minted,
        "stocked_on": row.stocked_on.isoformat(),
    }


async def list_stock(session: AsyncSession, shop_id: str) -> list[dict]:
    rows = (await session.execute(
        select(ShopStock)
        .where(ShopStock.shop_id == shop_id)
        .order_by(ShopStock.slot)
    )).scalars().all()
    return [stock_view(row) for row in rows]


async def ensure_daily_stock(
    session: AsyncSession,
    definition: ShopDefinition,
    *,
    day: date | None = None,
    rng: random.Random | None = None,
) -> list[dict]:
    """Refresh once per UTC day and return the globally remaining stock.

    Empty stock is a valid remembered state: ShopState prevents sold-out shops
    from refilling until the date changes.
    """
    day = day or utc_day()
    state = await session.get(ShopState, definition.id, with_for_update=True)
    if state is not None and state.last_restock_on == day:
        return await list_stock(session, definition.id)

    if state is None:
        state = ShopState(shop_id=definition.id, last_restock_on=day)
        session.add(state)
    else:
        state.last_restock_on = day
    await session.execute(delete(ShopStock).where(ShopStock.shop_id == definition.id))

    created = 0
    for slot in range(definition.stock_size):
        item, minted = await spawn_loot(
            session,
            weights=definition.rarity_weights,
            rng=rng,
            room_content_id=definition.room_content_id,
        )
        if item is None:
            continue
        session.add(ShopStock(
            shop_id=definition.id,
            slot=slot,
            item=item,
            price=price_for(item, rng),
            minted=minted,
            stocked_on=day,
        ))
        created += 1

    if created == 0:
        await session.rollback()
        raise RuntimeError("the item pool is empty")
    await session.commit()
    return await list_stock(session, definition.id)


async def purchase(
    session: AsyncSession,
    *,
    shop_id: str,
    slot: int,
    item_id: int,
    stocked_on: str,
    player_id: str,
    live_inventory: list,
) -> Purchase:
    """Atomically spend coins, persist the pack, and remove global stock."""
    if (
        not isinstance(slot, int)
        or not isinstance(item_id, int)
        or not isinstance(stocked_on, str)
    ):
        raise PurchaseError("That item is no longer on the counter.")

    stock = (await session.execute(
        select(ShopStock)
        .where(ShopStock.shop_id == shop_id, ShopStock.slot == slot)
        .with_for_update()
    )).scalar_one_or_none()
    if (
        stock is None
        or stock.item.get("id") != item_id
        or stock.stocked_on.isoformat() != stocked_on
    ):
        raise PurchaseError("Someone else has already bought that.")

    player = await session.get(PlayerRow, player_id, with_for_update=True)
    if player is None:
        raise PurchaseError("Your account could not be found.")
    if player.coins < stock.price:
        raise PurchaseError(f"You need {stock.price - player.coins} more coins.")

    inventory = inventory_with_item(live_inventory, stock.item)
    if inventory is None:
        raise PurchaseError("Your pack is full.")

    player.coins -= stock.price
    # Whole JSON-column assignment is required for SQLAlchemy change tracking.
    player.inventory = inventory
    item, price, bought_slot = stock.item, stock.price, stock.slot
    await session.delete(stock)
    await session.commit()
    return Purchase(
        item=item,
        price=price,
        inventory=inventory,
        coins=player.coins,
        slot=bought_slot,
    )


async def sell_item(
    session: AsyncSession,
    *,
    definition: ShopDefinition,
    slot: int,
    item_id: int,
    player_id: str,
    live_inventory: list,
) -> Sale:
    """Atomically remove one carried copy and credit its fixed buyback.

    The live pack is authoritative between persistence edges, just as it is
    for purchases. Slot plus item id form the stale-panel guard; equipped gear
    must be put away explicitly before a sale.
    """
    if not definition.buys_items:
        raise PurchaseError("This counter does not buy carried goods.")
    if not isinstance(slot, int) or not isinstance(item_id, int):
        raise PurchaseError("That item is no longer in your pack.")
    if not isinstance(live_inventory, list) or not 0 <= slot < len(live_inventory):
        raise PurchaseError("That item is no longer in your pack.")

    candidate = deepcopy(live_inventory)
    held = candidate[slot]
    if (
        not isinstance(held, dict)
        or not isinstance(held.get("item"), dict)
        or held["item"].get("id") != item_id
        or not isinstance(held.get("quantity"), int)
        or held["quantity"] <= 0
    ):
        raise PurchaseError("That item is no longer in your pack.")
    if held.get("equipped"):
        raise PurchaseError("Put that item away before selling it.")

    item = held["item"]
    price = buyback_price_for(item)
    held["quantity"] -= 1
    if held["quantity"] == 0:
        candidate.pop(slot)

    player = await session.get(PlayerRow, player_id, with_for_update=True)
    if player is None:
        raise PurchaseError("Your account could not be found.")
    player.inventory = candidate
    player.coins += price
    await session.commit()
    return Sale(
        item=item,
        price=price,
        inventory=candidate,
        coins=player.coins,
        slot=slot,
    )
