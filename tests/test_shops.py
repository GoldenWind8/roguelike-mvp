"""Exploration shops: daily global stock, coins, and atomic buying."""
from datetime import date, timedelta
import random

import pytest
from sqlalchemy import delete, func, select

from backend.config import PLAYER_STARTING_COINS
from backend.models import ShopStock
from backend.object_defs import get_object_definition
from backend.player_store import get_player_row, make_live_player, register_player
from backend.seeds import seed_items_if_missing
from backend.shop_defs import get_shop_for_object
from backend.shop_store import (
    BUYBACK_PRICES,
    PRICE_RANGES,
    PurchaseError,
    buyback_price_for,
    ensure_daily_stock,
    purchase,
    sell_item,
)
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


async def test_reed_market_fence_is_an_authored_buyback_counter():
    shop = get_shop_for_object("drazna_market_stores")
    counter = get_object_definition("reed_market_fence")

    assert shop is not None
    assert shop.id == "drazna_reed_market_fence"
    assert shop.room_content_id == "drazna_reed_market"
    assert shop.buys_items is True
    assert counter is not None
    assert counter.interaction == "shop"


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


async def test_fence_sale_credits_coins_and_removes_exactly_one_copy(
    session,
    no_item_llm,
):
    await seed_items_if_missing(session)
    account = await register_player(session, "salvager", "password")
    fence = get_shop_for_object("drazna_market_stores")
    offer = (await ensure_daily_stock(
        session,
        fence,
        day=date(2042, 3, 5),
        rng=random.Random(4),
    ))[0]["item"]
    inventory = [{"item": offer, "quantity": 2, "equipped": False}]

    first = await sell_item(
        session,
        definition=fence,
        slot=0,
        item_id=offer["id"],
        player_id=account.id,
        live_inventory=inventory,
    )

    assert first.price == buyback_price_for(offer)
    assert first.coins == PLAYER_STARTING_COINS + first.price
    assert first.inventory == [{
        "item": offer,
        "quantity": 1,
        "equipped": False,
    }]
    saved = await get_player_row(session, account.id)
    assert saved.coins == first.coins
    assert saved.inventory == first.inventory

    second = await sell_item(
        session,
        definition=fence,
        slot=0,
        item_id=offer["id"],
        player_id=account.id,
        live_inventory=first.inventory,
    )
    assert second.inventory == []
    assert second.coins == PLAYER_STARTING_COINS + 2 * first.price


async def test_fence_refuses_equipped_stale_and_non_buyback_sales(
    session,
    no_item_llm,
):
    await seed_items_if_missing(session)
    account = await register_player(session, "carefulseller", "password")
    fence = get_shop_for_object("drazna_market_stores")
    ordinary_shop = get_shop_for_object("oakrun_general_goods_shop")
    item = (await ensure_daily_stock(
        session,
        fence,
        day=date(2042, 3, 5),
        rng=random.Random(4),
    ))[0]["item"]
    equipped = [{"item": item, "quantity": 1, "equipped": True}]

    with pytest.raises(PurchaseError, match="Put that item away"):
        await sell_item(
            session,
            definition=fence,
            slot=0,
            item_id=item["id"],
            player_id=account.id,
            live_inventory=equipped,
        )
    with pytest.raises(PurchaseError, match="no longer in your pack"):
        await sell_item(
            session,
            definition=fence,
            slot=0,
            item_id=item["id"] + 1,
            player_id=account.id,
            live_inventory=[{**equipped[0], "equipped": False}],
        )
    with pytest.raises(PurchaseError, match="does not buy"):
        await sell_item(
            session,
            definition=ordinary_shop,
            slot=0,
            item_id=item["id"],
            player_id=account.id,
            live_inventory=[{**equipped[0], "equipped": False}],
        )
    saved = await get_player_row(session, account.id)
    assert saved.coins == PLAYER_STARTING_COINS
    assert saved.inventory == []


def test_buyback_cannot_arbitrage_and_worst_case_finds_fund_return():
    for rarity, buyback in BUYBACK_PRICES.items():
        assert 0 < buyback < PRICE_RANGES[rarity][0]

    # The long road costs 24. After paying it from the 30-coin starting purse,
    # five guaranteed-minimum common finds sold at Teo's counter cover another
    # crossing with two coins left. Drazna contains fifteen authored chests,
    # and generated frontier rooms each add at least one more.
    after_outbound = PLAYER_STARTING_COINS - 24
    after_five_common_sales = after_outbound + 5 * BUYBACK_PRICES["common"]
    assert after_outbound == 6
    assert after_five_common_sales == 26
