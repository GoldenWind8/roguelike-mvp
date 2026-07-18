"""Item delivery through the engine (consume/throw/weapon attack) and the
spawn_loot roll. The handler halves of docs/LOOT.md: one atom vocabulary,
every delivery path, plus the LLM-with-fallback source decision.
"""
import random

import pytest

from backend.entities import Position
from backend.events import EventType
from backend.inventory import add_item, equip
from backend.loot import roll_rarity, spawn_loot
from backend.room_engine import RoomEngine
from backend.seeds import seed_items_if_missing
import backend.loot as loot_module


def _item(item_id, item_type, name, payload) -> dict:
    return {"id": item_id, "name": name, "description": "d", "rarity": "common",
            "type": item_type, "art": {"kind": "emoji", "value": "❓"},
            "payload": payload, "origin": "seed"}


POTION = _item(1, "consumable", "Health Potion",
               {"effects": [{"kind": "restore_hp", "amount": 12}]})
FURY = _item(2, "consumable", "Potion of Fury",
             {"effects": [{"kind": "stat_mod", "stat": "attack_damage",
                           "amount": 8, "duration_s": 60}]})
BOMB = _item(3, "throwable", "Bomb",
             {"throw_range": 4, "area": {"shape": "radius", "size": 1},
              "effects": [{"kind": "damage", "amount": 3}]})
BOW = _item(4, "weapon", "Bow", {"damage": 34, "range": 4})


def event_types(events):
    return [e.event_type for e in events]


# --- consume -------------------------------------------------------------------


def test_consume_heals_and_spends_the_item(make_template):
    engine = RoomEngine(make_template())        # empty room -> exploration
    hero, _ = engine.join("Hero")
    hero.hp = 50
    add_item(hero, POTION)

    events, resolved = engine.submit_action(hero.id, {"action_type": "consume", "slot": 0})

    assert resolved
    assert EventType.ITEM_CONSUMED in event_types(events)
    heal = next(e for e in events if e.event_type is EventType.ENTITY_HEALED)
    assert heal.data["amount"] == 12
    assert hero.hp == 62
    assert hero.inventory == []                 # the copy is spent


def test_consume_buff_lands_as_timed_effect(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    add_item(hero, FURY)

    events, _ = engine.submit_action(hero.id, {"action_type": "consume", "slot": 0})

    applied = next(e for e in events if e.event_type is EventType.EFFECT_APPLIED)
    assert applied.data == {"target_id": hero.id, "stat": "attack_damage",
                            "amount": 8, "duration_s": 60,
                            "source": "Potion of Fury", "source_id": hero.id}
    assert len(hero.active_effects) == 1


def test_consume_validates_slot_and_type(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    add_item(hero, BOW)

    for data in ({"action_type": "consume", "slot": 5},
                 {"action_type": "consume", "slot": 0},      # a bow, not a snack
                 {"action_type": "consume"}):
        events, resolved = engine.submit_action(hero.id, data)
        assert not resolved
        assert event_types(events) == [EventType.INVALID_ACTION]
    assert hero.inventory[0]["quantity"] == 1               # nothing spent


# --- throw ---------------------------------------------------------------------


def test_throw_hits_every_actor_in_area(make_template):
    # Goblin at (3,3), second one at (3,2) — radius 1 catches both; the
    # thrower at (1,1) stands outside the blast.
    engine = RoomEngine(make_template(enemies=((3, 3), (3, 2))))
    hero, _ = engine.join("Hero")
    add_item(hero, BOMB)

    events, resolved = engine.submit_action(
        hero.id, {"action_type": "throw", "slot": 0, "target_tile": [3, 3]})

    assert resolved
    thrown = next(e for e in events if e.event_type is EventType.ITEM_THROWN)
    assert thrown.data["item"]["name"] == "Bomb"
    damaged = [e.data["target_id"] for e in events
               if e.event_type is EventType.ENTITY_DAMAGED]
    assert set(damaged) == {"enemy_1", "enemy_2"}
    assert hero.inventory == []


def test_throw_range_comes_from_the_item(make_template):
    engine = RoomEngine(make_template(width=9, height=9, enemies=((7, 7),)))
    hero, _ = engine.join("Hero")
    add_item(hero, BOMB)

    events, resolved = engine.submit_action(
        hero.id, {"action_type": "throw", "slot": 0, "target_tile": [7, 7]})

    assert not resolved
    assert event_types(events) == [EventType.INVALID_ACTION]
    assert hero.inventory[0]["quantity"] == 1


# --- weapon attack -------------------------------------------------------------


def test_equipped_bow_extends_attack_reach(make_template):
    engine = RoomEngine(make_template(width=9, height=9, enemies=((4, 1),)))
    hero, _ = engine.join("Hero")                            # spawns at (1,1)

    # Bare hands: three tiles away is out of reach...
    events, resolved = engine.submit_action(
        hero.id, {"action_type": "attack", "target_id": "enemy_1"})
    assert not resolved and event_types(events) == [EventType.INVALID_ACTION]

    # ...the bow's range-4 makes the same attack legal, at weapon damage.
    add_item(hero, BOW)
    equip(hero, 0)
    events, resolved = engine.submit_action(
        hero.id, {"action_type": "attack", "target_id": "enemy_1"})
    assert resolved
    attack = next(e for e in events if e.event_type is EventType.PLAYER_ATTACKED)
    assert attack.data["damage"] == 34                       # goblin defense 0


# --- the roll ------------------------------------------------------------------


def test_roll_rarity_honors_weights():
    rng = random.Random(42)
    rolls = {roll_rarity({"legendary": 1}, rng) for _ in range(5)}
    assert rolls == {"legendary"}
    counts = {"common": 0, "rare": 0, "legendary": 0}
    for _ in range(2000):
        counts[roll_rarity(None, rng)] += 1
    assert counts["common"] > counts["rare"] > counts["legendary"] > 0


@pytest.mark.asyncio
async def test_spawn_loot_draws_the_pool_without_a_premium_tier(session, monkeypatch):
    await seed_items_if_missing(session)
    monkeypatch.setattr(loot_module, "tier_available", lambda tier: False)
    item, minted = await spawn_loot(session, weights={"legendary": 1},
                                    rng=random.Random(7))
    assert not minted
    assert item["rarity"] == "legendary"


@pytest.mark.asyncio
async def test_spawn_loot_llm_failure_falls_back_to_pool(session, monkeypatch):
    await seed_items_if_missing(session)
    monkeypatch.setattr(loot_module, "tier_available", lambda tier: True)
    monkeypatch.setattr(loot_module, "LOOT_LLM_CHANCE", 1.0)

    async def exploding_generate(session_, rarity):
        raise RuntimeError("provider down")
    monkeypatch.setattr(loot_module, "generate_item", exploding_generate)

    item, minted = await spawn_loot(session, rng=random.Random(7))
    assert not minted
    assert item is not None                     # the chest still pays out


@pytest.mark.asyncio
async def test_spawn_loot_mints_when_the_llm_delivers(session, monkeypatch):
    monkeypatch.setattr(loot_module, "tier_available", lambda tier: True)
    monkeypatch.setattr(loot_module, "LOOT_LLM_CHANCE", 1.0)
    fake = {"id": 99, "name": "Whispered Knife", "rarity": "rare"}

    async def fake_generate(session_, rarity):
        return {**fake, "rarity": rarity}
    monkeypatch.setattr(loot_module, "generate_item", fake_generate)

    item, minted = await spawn_loot(session, weights={"rare": 1},
                                    rng=random.Random(7))
    assert minted
    assert item["name"] == "Whispered Knife" and item["rarity"] == "rare"
