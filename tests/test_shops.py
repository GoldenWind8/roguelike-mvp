"""Exploration shops: daily global stock, coins, and atomic buying."""
from datetime import date, timedelta
import random

import pytest
from sqlalchemy import delete, func, select

from backend.models import ShopStock
from backend.player_store import get_player_row, make_live_player, register_player
from backend.seeds import seed_items_if_missing
from backend.shop_defs import get_shop_for_object
from backend.shop_store import PurchaseError, ensure_daily_stock, purchase
import backend.loot as loot_module


@pytest.fixture
def no_item_llm(monkeypatch):
    """Shop tests must never depend on a configured external provider."""
    monkeypatch.setattr(loot_module, "tier_available", lambda _tier: False)


async def test_shop_definition_binds_to_stable_oakrun_object():
    shop = get_shop_for_object("oakrun_general_goods_shop")
    assert shop is not None
    assert shop.id == "oakrun_general_goods"
    assert shop.stock_size == 5


async def test_daily_stock_is_small_and_does_not_refill_after_sellout(session, no_item_llm):
    await seed_items_if_missing(session)
    shop = get_shop_for_object("oakrun_general_goods_shop")
    today = date(2042, 3, 5)

    first = await ensure_daily_stock(
        session, shop, day=today, rng=random.Random(7),
    )
    assert len(first) == shop.stock_size
    assert all(entry["stocked_on"] == today.isoformat() for entry in first)

    # Reopening today returns the same persistent global slots.
    again = await ensure_daily_stock(
        session, shop, day=today, rng=random.Random(999),
    )
    assert again == first

    # A globally sold-out shop stays empty today...
    await session.execute(delete(ShopStock).where(ShopStock.shop_id == shop.id))
    await session.commit()
    assert await ensure_daily_stock(session, shop, day=today) == []

    # ...and gets one fresh small selection on the next UTC date.
    tomorrow = await ensure_daily_stock(
        session, shop, day=today + timedelta(days=1), rng=random.Random(8),
    )
    assert len(tomorrow) == shop.stock_size
    assert tomorrow != first


async def test_purchase_spends_coins_adds_to_pack_and_removes_global_stock(session, no_item_llm):
    await seed_items_if_missing(session)
    account = await register_player(session, "shopper", "password")
    live = make_live_player(account)
    shop = get_shop_for_object("oakrun_general_goods_shop")
    stock = await ensure_daily_stock(
        session, shop, day=date(2042, 3, 5), rng=random.Random(4),
    )
    offer = stock[0]

    bought = await purchase(
        session,
        shop_id=shop.id,
        slot=offer["slot"],
        item_id=offer["item"]["id"],
        stocked_on=offer["stocked_on"],
        player_id=account.id,
        live_inventory=live.inventory,
    )

    assert bought.item == offer["item"]
    assert bought.coins == 30 - offer["price"]
    assert bought.inventory[0]["item"] == offer["item"]
    saved = await get_player_row(session, account.id)
    assert saved.coins == bought.coins
    assert saved.inventory == bought.inventory
    remaining = (await session.execute(
        select(func.count()).select_from(ShopStock).where(ShopStock.shop_id == shop.id)
    )).scalar_one()
    assert remaining == shop.stock_size - 1

    # The deleted row is global truth: the same or another player cannot claim it.
    second = await register_player(session, "latebuyer", "password")
    with pytest.raises(PurchaseError, match="already bought"):
        await purchase(
            session,
            shop_id=shop.id,
            slot=offer["slot"],
            item_id=offer["item"]["id"],
            stocked_on=offer["stocked_on"],
            player_id=second.id,
            live_inventory=[],
        )


async def test_yesterdays_panel_cannot_buy_todays_reused_slot(session, no_item_llm):
    await seed_items_if_missing(session)
    account = await register_player(session, "patient", "password")
    shop = get_shop_for_object("oakrun_general_goods_shop")
    old = (await ensure_daily_stock(
        session, shop, day=date(2042, 3, 5), rng=random.Random(2),
    ))[0]
    await ensure_daily_stock(
        session, shop, day=date(2042, 3, 6), rng=random.Random(2),
    )

    with pytest.raises(PurchaseError, match="already bought"):
        await purchase(
            session,
            shop_id=shop.id,
            slot=old["slot"],
            item_id=old["item"]["id"],
            stocked_on=old["stocked_on"],
            player_id=account.id,
            live_inventory=[],
        )
