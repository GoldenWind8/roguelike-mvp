"""The hunger clock (docs/LOOT.md Decision 5): drain, well-fed regen,
starvation, food atoms, and the chest item-count roll that shipped with it.
"""
import random

import pytest

from backend.config import HUNGER_MAX, HUNGER_REGEN_COST
from backend.events import EventType
from backend.hunger import tick_room_hunger
from backend.inventory import add_item
from backend.items import validate_item
from backend.loot import roll_item_count
from backend.room_engine import RoomEngine


BREAD = {"id": 9, "name": "Traveler's Bread", "description": "d",
         "rarity": "common", "type": "consumable",
         "art": {"kind": "emoji", "value": "🍞"},
         "payload": {"effects": [{"kind": "restore_hunger", "amount": 25}]},
         "origin": "seed"}


def event_types(events):
    return [e.event_type for e in events]


# --- the drain -----------------------------------------------------------------


def test_hunger_drains_with_time(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    hero.hunger = 50.0

    events, visible = tick_room_hunger(engine.room, dt=9.0)

    assert hero.hunger == pytest.approx(49.0)   # 100/900 per second
    assert events == []                          # nothing dramatic happened
    assert visible                               # ...but the bar moved a point


def test_small_ticks_are_invisible_until_a_point_drops(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    hero.hunger = 50.0

    _, visible = tick_room_hunger(engine.room, dt=2.0)

    assert hero.hunger < 50.0
    assert not visible                           # rounds to 50 either way


# --- well fed: the Minecraft rule ----------------------------------------------


def test_well_fed_knits_wounds_for_extra_hunger(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    hero.hp = 90
    hero.hunger = float(HUNGER_MAX)

    events, _ = tick_room_hunger(engine.room, dt=2.0)

    heal = next(e for e in events if e.event_type is EventType.ENTITY_HEALED)
    assert heal.data["amount"] == 1
    assert hero.hp == 91
    # base drain plus the regen surcharge
    assert hero.hunger == pytest.approx(HUNGER_MAX - 100 / 900 * 2 - HUNGER_REGEN_COST)


def test_no_regen_below_the_threshold_or_at_full_hp(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")

    hero.hp, hero.hunger = 90, 50.0              # hungry: no free healing
    events, _ = tick_room_hunger(engine.room, dt=2.0)
    assert EventType.ENTITY_HEALED not in event_types(events)

    hero.hp, hero.hunger = hero.max_hp, float(HUNGER_MAX)   # full: nothing to knit
    events, _ = tick_room_hunger(engine.room, dt=2.0)
    assert EventType.ENTITY_HEALED not in event_types(events)


# --- starving: the Don't Starve rule --------------------------------------------


def test_hitting_zero_announces_once_then_chips_hp(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    hero.hunger = 0.1

    events, _ = tick_room_hunger(engine.room, dt=2.0)
    assert EventType.PLAYER_STARVING in event_types(events)
    damage = next(e for e in events if e.event_type is EventType.ENTITY_DAMAGED)
    assert damage.data["damage"] == 1            # min-1 clamp beats armor
    assert damage.data["cause"] == "starvation"
    assert hero.hunger == 0.0

    events, _ = tick_room_hunger(engine.room, dt=2.0)
    assert EventType.PLAYER_STARVING not in event_types(events)   # said once
    assert EventType.ENTITY_DAMAGED in event_types(events)        # still hurts


def test_starvation_can_kill(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    hero.hp, hero.hunger = 1, 0.0

    events, _ = tick_room_hunger(engine.room, dt=2.0)

    assert EventType.PLAYER_DIED in event_types(events)
    assert not hero.is_alive


def test_the_dead_do_not_hunger(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    hero.is_alive = False
    hero.hunger = 50.0

    events, visible = tick_room_hunger(engine.room, dt=60.0)

    assert events == [] and not visible
    assert hero.hunger == 50.0


# --- eating --------------------------------------------------------------------


def test_consume_food_refills_the_meter(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    hero.hunger = 50.0
    add_item(hero, BREAD)

    events, _ = engine.submit_action(hero.id, {"action_type": "consume", "slot": 0})

    fed = next(e for e in events if e.event_type is EventType.HUNGER_RESTORED)
    assert fed.data["amount"] == 25
    assert hero.hunger == 75.0


def test_eating_clamps_at_the_ceiling(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    hero.hunger = 90.0
    add_item(hero, BREAD)

    events, _ = engine.submit_action(hero.id, {"action_type": "consume", "slot": 0})

    fed = next(e for e in events if e.event_type is EventType.HUNGER_RESTORED)
    assert fed.data["amount"] == 10
    assert hero.hunger == float(HUNGER_MAX)


# --- the vocabulary gate --------------------------------------------------------


def test_restore_hunger_validates_like_every_atom():
    validate_item({k: v for k, v in BREAD.items() if k not in ("id", "origin")})

    over_cap = {**BREAD, "payload": {"effects": [{"kind": "restore_hunger", "amount": 999}]}}
    with pytest.raises(ValueError, match="restore_hunger"):
        validate_item({k: v for k, v in over_cap.items() if k not in ("id", "origin")})

    on_gear = {**BREAD, "type": "wearable"}
    with pytest.raises(ValueError, match="not allowed"):
        validate_item({k: v for k, v in on_gear.items() if k not in ("id", "origin")})


# --- how many things in a chest -------------------------------------------------


def test_roll_item_count_stays_in_range():
    rng = random.Random(7)
    counts = {roll_item_count(rng=rng) for _ in range(200)}
    assert counts == {1, 2, 3}


def test_roll_item_count_weights_are_data():
    assert roll_item_count({2: 1}, random.Random(1)) == 2
