"""The pack rules (backend/inventory.py) and effective stats.

Load-bearing: stacking vs slot-cap, the one-weapon invariant, effective
stats summing base + gear + timed effects, hp clamping when a ceiling moves,
and the pack surviving the save/load round trip.
"""
import pytest

from backend.config import INVENTORY_SLOTS, PLAYER_MAX_HP
from backend.entities import Enemy, Player, Position
from backend.inventory import (
    add_item,
    add_timed_effect,
    attack_power,
    attack_range,
    effective_stat,
    equip,
    equipped_weapon,
    prune_expired,
    remove_one,
    unequip,
)
from backend.player_store import make_live_player, register_player, save_players


def _player(**kw) -> Player:
    defaults = dict(id="player_t", name="Tess", position=Position(1, 1),
                    hp=50, max_hp=100)
    defaults.update(kw)
    return Player(**defaults)


def _item(item_id=1, item_type="consumable", name="Tonic", payload=None) -> dict:
    if payload is None:
        payload = {"effects": [{"kind": "restore_hp", "amount": 5}]}
    return {"id": item_id, "name": name, "description": "d", "rarity": "common",
            "type": item_type, "art": {"kind": "emoji", "value": "🧪"},
            "payload": payload, "origin": "seed"}


SWORD = _item(10, "weapon", "Sword", {"damage": 42, "range": 1})
BOW = _item(11, "weapon", "Bow", {"damage": 34, "range": 4})
CAP = _item(12, "wearable", "Cap",
            {"effects": [{"kind": "stat_mod", "stat": "defense", "amount": 2}]})
BULK_ARMOR = _item(13, "wearable", "Aegis",
                   {"effects": [{"kind": "stat_mod", "stat": "max_hp", "amount": 20}]})


# --- stacking and the slot cap -------------------------------------------------


def test_stackables_stack_and_equipables_do_not():
    p = _player()
    assert add_item(p, _item(1)) == 0
    assert add_item(p, _item(1)) == 0          # same id joins the stack
    assert p.inventory[0]["quantity"] == 2
    assert add_item(p, SWORD) == 1
    assert add_item(p, SWORD) == 2             # second sword takes its own slot
    assert len(p.inventory) == 3


def test_full_pack_refuses_new_items_but_not_stacks():
    p = _player()
    for i in range(INVENTORY_SLOTS):
        assert add_item(p, _item(100 + i)) is not None
    assert add_item(p, _item(999)) is None          # a NEW item bounces
    assert add_item(p, _item(100)) is not None      # an existing stack absorbs
    assert p.inventory[0]["quantity"] == 2


def test_remove_one_spends_the_stack_then_the_slot():
    p = _player()
    add_item(p, _item(1))
    add_item(p, _item(1))
    assert remove_one(p, 0)["id"] == 1
    assert p.inventory[0]["quantity"] == 1
    remove_one(p, 0)
    assert p.inventory == []


# --- equipping -----------------------------------------------------------------


def test_only_one_weapon_equipped_at_a_time():
    p = _player()
    add_item(p, SWORD)
    add_item(p, BOW)
    assert equip(p, 0) is None
    assert equip(p, 1) is None                     # bow in, sword auto-out
    assert [s["equipped"] for s in p.inventory] == [False, True]
    assert equipped_weapon(p)["range"] == 4


def test_multiple_wearables_stack_their_bonuses():
    p = _player()
    add_item(p, CAP)
    add_item(p, _item(14, "wearable", "Buckler",
                      {"effects": [{"kind": "stat_mod", "stat": "defense", "amount": 3}]}))
    equip(p, 0)
    equip(p, 1)
    assert effective_stat(p, "defense") == p.defense + 5


def test_consumables_refuse_to_equip():
    p = _player()
    add_item(p, _item(1))
    assert "can't equip" in equip(p, 0)


def test_unequip_clamps_hp_when_the_ceiling_drops():
    p = _player(hp=100)
    add_item(p, BULK_ARMOR)
    equip(p, 0)
    p.hp = 115                                      # healed up under +20 max_hp
    assert unequip(p, 0) is None
    assert p.hp == 100                              # ceiling moved, hp followed


# --- effective stats and the weapon seam --------------------------------------


def test_attack_power_weapon_replaces_fists_and_bonuses_add():
    p = _player()
    assert attack_power(p) == p.attack_damage       # bare hands
    add_item(p, SWORD)
    equip(p, 0)
    assert attack_power(p) == 42                    # sword REPLACES base
    add_timed_effect(p, "attack_damage", 8, 60, "Potion of Fury")
    assert attack_power(p) == 50                    # buffs add on top
    assert attack_range(p) == 1


def test_attack_range_comes_from_the_weapon():
    p = _player()
    add_item(p, BOW)
    equip(p, 0)
    assert attack_range(p) == 4


def test_timed_effects_expire_on_the_world_clock(monkeypatch):
    p = _player()
    t = 1000.0
    monkeypatch.setattr("backend.inventory.world_clock.now", lambda: t)
    add_timed_effect(p, "defense", 4, duration_s=30, source="x")
    assert effective_stat(p, "defense") == p.defense + 4

    t = 1031.0
    expired = prune_expired(p)
    assert [e["stat"] for e in expired] == ["defense"]
    assert effective_stat(p, "defense") == p.defense
    assert prune_expired(p) == []                   # second sweep finds nothing


def test_expiring_max_hp_buff_clamps_hp(monkeypatch):
    p = _player(hp=100)
    t = 1000.0
    monkeypatch.setattr("backend.inventory.world_clock.now", lambda: t)
    add_timed_effect(p, "max_hp", 20, duration_s=30, source="x")
    p.hp = 118
    t = 1031.0
    prune_expired(p)
    assert p.hp == 100


def test_enemies_carry_timed_effects_too():
    # The atoms are generic (docs/LOOT.md): a poison flask debuffs a goblin
    # through exactly the machinery that buffs a player.
    goblin = Enemy(id="enemy_1", name="Goblin", position=Position(2, 2),
                   hp=6, max_hp=6, defense=3, attack_damage=1)
    add_timed_effect(goblin, "defense", -2, 120, "Poison Flask")
    assert effective_stat(goblin, "defense") == 1


# --- persistence ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_pack_survives_the_save_load_round_trip(session):
    row = await register_player(session, "tess", "hunter2")
    live = make_live_player(row)
    add_item(live, SWORD)
    add_item(live, _item(1))
    add_item(live, _item(1))
    equip(live, 0)
    live.position = Position(3, 4)

    await save_players(session, [live], room_id=None)
    reloaded = make_live_player(await session.get(type(row), row.id))

    assert reloaded.inventory == live.inventory
    assert reloaded.inventory[0]["equipped"] is True
    assert reloaded.inventory[1]["quantity"] == 2
