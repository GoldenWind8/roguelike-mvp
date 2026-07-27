"""Drazna's scoped item backfill and content-aware chest accent."""
from pathlib import Path
import random

import pytest
from sqlalchemy import func, select

import backend.loot as loot_module
from backend.item_store import insert_item
from backend.items import validate_item
from backend.loot import spawn_loot
from backend.models import ItemDef, Room
from backend.regional_items import (
    DRAZNA_ITEMS,
    region_for_room_content_id,
    regional_art_values,
)
from backend.room_loader import load_room
from backend.seeds import STARTER_ITEMS, seed_items_if_missing


ROOT = Path(__file__).resolve().parents[1]


def _unrelated_item(*, name: str = "Player-Grown Relic", rarity: str = "common") -> dict:
    return {
        "name": name,
        "description": "An unrelated item already living in this world's pool.",
        "rarity": rarity,
        "type": "consumable",
        "art": {"kind": "emoji", "value": "🫙"},
        "payload": {"effects": [{"kind": "restore_hp", "amount": 5}]},
    }


def test_drazna_items_pass_the_shared_gate_and_have_bundled_art():
    assert 4 <= len(DRAZNA_ITEMS) <= 6
    for definition in DRAZNA_ITEMS:
        validate_item(definition)
        art = definition["art"]
        assert art["kind"] == "url"
        assert (ROOT / "frontend-react" / "public" / art["value"].lstrip("/")).is_file()


def test_authored_content_id_selects_only_the_drazna_scope():
    assert region_for_room_content_id("drazna_undertide") == "drazna"
    assert region_for_room_content_id("drazna_birch_heights") == "drazna"
    assert region_for_room_content_id("oakrun_market_square") is None
    assert region_for_room_content_id(None) is None


@pytest.mark.asyncio
async def test_loader_preserves_content_id_for_loot_context(session):
    room = Room(
        content_id="drazna_test_cache",
        name="Drazna Test Cache",
        width=3,
        height=3,
        terrain=["###", "#.#", "###"],
        objects=[],
        spawn_points=[[1, 1]],
        enemy_spawns=[],
    )
    session.add(room)
    await session.commit()

    template = await load_room(session, room.id)
    assert template.content_id == "drazna_test_cache"


@pytest.mark.asyncio
async def test_regional_backfill_preserves_grown_pool_and_is_idempotent(session):
    original = await insert_item(session, _unrelated_item(), origin="llm")
    await session.commit()

    await seed_items_if_missing(session)
    rows = (await session.execute(select(ItemDef).order_by(ItemDef.id))).scalars().all()
    assert len(rows) == 1 + len(DRAZNA_ITEMS)
    assert rows[0].id == original["id"]
    assert rows[0].name == original["name"]
    assert rows[0].origin == "llm"
    # A non-empty player-grown pool must not receive the generic starter batch.
    assert not ({item["name"] for item in STARTER_ITEMS} & {row.name for row in rows})
    assert regional_art_values() == {
        row.art["value"] for row in rows[1:]
    }

    ids_before = [row.id for row in rows]
    await seed_items_if_missing(session)
    rows_after = (
        await session.execute(select(ItemDef).order_by(ItemDef.id))
    ).scalars().all()
    assert [row.id for row in rows_after] == ids_before


@pytest.mark.asyncio
async def test_drazna_draws_prefer_but_do_not_exclude_global_items(
    session,
    monkeypatch,
):
    await seed_items_if_missing(session)
    monkeypatch.setattr(loot_module, "LOOT_LLM_CHANCE", 0.0)
    rng = random.Random(731)
    regional = regional_art_values("drazna")
    draws = [
        (
            await spawn_loot(
                session,
                weights={"common": 1},
                rng=rng,
                room_content_id="drazna_undertide",
            )
        )[0]
        for _ in range(500)
    ]
    regional_count = sum(item["art"]["value"] in regional for item in draws)

    assert 320 <= regional_count <= 410
    assert regional_count < len(draws)


@pytest.mark.asyncio
async def test_non_drazna_draws_are_not_diluted_by_scoped_backfill(
    session,
    monkeypatch,
):
    await seed_items_if_missing(session)
    monkeypatch.setattr(loot_module, "LOOT_LLM_CHANCE", 0.0)
    regional = regional_art_values()
    rng = random.Random(413)

    for _ in range(120):
        item, minted = await spawn_loot(
            session,
            weights={"common": 1},
            rng=rng,
            room_content_id="oakrun_market_square",
        )
        assert not minted
        assert item["art"]["value"] not in regional


@pytest.mark.asyncio
async def test_drazna_region_falls_back_when_rolled_rarity_has_no_local_item(
    session,
    monkeypatch,
):
    await insert_item(
        session,
        _unrelated_item(name="Unrelated Legendary", rarity="legendary")
        | {"payload": {"effects": [{"kind": "restore_hp", "amount": 80}]}},
        origin="llm",
    )
    await session.commit()
    monkeypatch.setattr(loot_module, "LOOT_LLM_CHANCE", 0.0)

    item, minted = await spawn_loot(
        session,
        weights={"legendary": 1},
        rng=random.Random(9),
        room_content_id="drazna_gate_seven",
    )
    assert not minted
    assert item["name"] == "Unrelated Legendary"
    count = (await session.execute(select(func.count(ItemDef.id)))).scalar_one()
    assert count == 1
