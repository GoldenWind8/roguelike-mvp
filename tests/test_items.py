"""The item contract (backend/items.py) + the pool at rest (item_store).

The load-bearing assertions: every STARTER_ITEM passes the same gate LLM
output will pass; the gate rejects each way an item can lie (bad type, bad
atom, over-cap numbers, wearable with a timer); clamping pulls hot numbers
into the rarity band instead of rejecting them.
"""
import random

import pytest

from backend.items import (
    ItemType,
    Rarity,
    RARITY_CAPS,
    clamp_item,
    equipable,
    stackable,
    validate_item,
)
from backend.item_store import draw_random, insert_item, pool_is_empty
from backend.seeds import STARTER_ITEMS, seed_items_if_missing


def _item(**overrides) -> dict:
    """A minimal valid consumable to mutate per test."""
    base = {
        "name": "Test Tonic",
        "description": "For testing what ails you.",
        "rarity": "common",
        "type": "consumable",
        "art": {"kind": "emoji", "value": "🧪"},
        "payload": {"effects": [{"kind": "restore_hp", "amount": 5}]},
    }
    base.update(overrides)
    return base


# --- the gate ----------------------------------------------------------------


def test_every_starter_item_passes_the_gate():
    for data in STARTER_ITEMS:
        validate_item(data)  # raises on failure


def test_starter_pool_covers_every_type_and_rarity():
    # The pool must exercise the whole vocabulary — an atom or type with no
    # live item is untested content.
    types = {i["type"] for i in STARTER_ITEMS}
    rarities = {i["rarity"] for i in STARTER_ITEMS}
    kinds = {a["kind"] for i in STARTER_ITEMS
             for a in i["payload"].get("effects", [])}
    assert types == {t.value for t in ItemType}
    assert rarities == {r.value for r in Rarity}
    assert kinds == {"stat_mod", "restore_hp", "restore_hunger", "damage"}


@pytest.mark.parametrize("mutation, complaint", [
    ({"name": ""}, "name"),
    ({"rarity": "mythic"}, "rarity"),
    ({"type": "artifact"}, "type"),
    ({"art": {"kind": "png", "value": "x"}}, "art.kind"),
    ({"payload": {"effects": []}}, "effects"),
    ({"payload": {"effects": [{"kind": "polymorph", "amount": 1}]}}, "unknown effect kind"),
])
def test_gate_rejects_bad_shapes(mutation, complaint):
    with pytest.raises(ValueError, match=complaint):
        validate_item(_item(**mutation))


def test_gate_rejects_wearable_with_timer():
    # A wearable's stat_mod lasts while equipped — a duration would fight the
    # equip lifecycle, so the contract forbids it outright.
    with pytest.raises(ValueError, match="duration_s"):
        validate_item(_item(type="wearable", payload={"effects": [
            {"kind": "stat_mod", "stat": "defense", "amount": 1, "duration_s": 30}]}))


def test_gate_requires_timer_on_consumable_stat_mod():
    with pytest.raises(ValueError, match="duration_s"):
        validate_item(_item(payload={"effects": [
            {"kind": "stat_mod", "stat": "defense", "amount": 1}]}))


def test_gate_rejects_over_cap_numbers_per_rarity():
    caps = RARITY_CAPS[Rarity.COMMON]
    with pytest.raises(ValueError, match="restore_hp"):
        validate_item(_item(payload={"effects": [
            {"kind": "restore_hp", "amount": caps["heal"] + 1}]}))
    with pytest.raises(ValueError, match="weapon damage"):
        validate_item(_item(type="weapon",
                            payload={"damage": caps["weapon_damage"] + 1, "range": 1}))
    with pytest.raises(ValueError, match="budget"):
        validate_item(_item(type="wearable", payload={"effects": [
            {"kind": "stat_mod", "stat": "defense", "amount": caps["stat_total"] + 1}]}))


def test_gate_rejects_damage_atom_on_consumable():
    with pytest.raises(ValueError, match="not allowed"):
        validate_item(_item(payload={"effects": [{"kind": "damage", "amount": 2}]}))


# --- clamping (the LLM forgiveness path) -------------------------------------


def test_clamp_pulls_numbers_into_the_rarity_band():
    hot = _item(type="weapon", payload={"damage": 999, "range": 99})
    clamped = clamp_item(hot)
    validate_item(clamped)
    caps = RARITY_CAPS[Rarity.COMMON]
    assert clamped["payload"]["damage"] == caps["weapon_damage"]
    assert hot["payload"]["damage"] == 999  # input untouched


def test_clamp_respects_stat_budget_across_atoms():
    hot = _item(type="wearable", payload={"effects": [
        {"kind": "stat_mod", "stat": "defense", "amount": 4},
        {"kind": "stat_mod", "stat": "max_hp", "amount": 400},
    ]})
    clamped = clamp_item(hot)
    validate_item(clamped)
    total = sum(abs(a["amount"]) for a in clamped["payload"]["effects"])
    assert total <= RARITY_CAPS[Rarity.COMMON]["stat_total"]


def test_clamp_leaves_structural_garbage_for_the_gate():
    # Clamp fixes magnitude, never shape — a broken shape must still be
    # rejected by validate_item, not silently "repaired".
    garbage = _item(payload={"effects": "many"})
    assert clamp_item(garbage)["payload"]["effects"] == "many"
    with pytest.raises(ValueError):
        validate_item(garbage)


# --- derived rules -----------------------------------------------------------


def test_stackability_is_derived_from_type():
    assert stackable("consumable") and stackable("throwable")
    assert not stackable("weapon") and not stackable("wearable")
    assert equipable("weapon") and equipable("wearable")
    assert not equipable("consumable")


# --- the pool at rest --------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_items_once_and_never_again(session):
    assert await pool_is_empty(session)
    await seed_items_if_missing(session)
    assert not await pool_is_empty(session)

    # Second call must be a no-op — an LLM-grown pool is never diluted.
    before = (await draw_random(session, "common", random.Random(1)))["id"]
    await seed_items_if_missing(session)
    view = await draw_random(session, "common", random.Random(1))
    assert view["id"] == before


@pytest.mark.asyncio
async def test_insert_validates_before_writing(session):
    with pytest.raises(ValueError):
        await insert_item(session, _item(rarity="mythic"), origin="llm")
    assert await pool_is_empty(session)


@pytest.mark.asyncio
async def test_draw_random_honors_rarity_and_falls_back(session):
    await insert_item(session, _item(name="Only Legendary", rarity="legendary",
                                     payload={"effects": [{"kind": "restore_hp", "amount": 80}]}),
                      origin="seed")
    rng = random.Random(7)
    # Exact rarity when the pool has it...
    hit = await draw_random(session, "legendary", rng)
    assert hit["name"] == "Only Legendary"
    # ...and ANY rarity instead of None when it doesn't (a roll must always
    # pay out once the pool is non-empty).
    fallback = await draw_random(session, "common", rng)
    assert fallback["name"] == "Only Legendary"


@pytest.mark.asyncio
async def test_draw_from_truly_empty_pool_is_none(session):
    assert await draw_random(session, "common", random.Random(1)) is None
