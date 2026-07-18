"""Tests for the room/tile model, validation, and seed round-trip (issue #20)."""
import copy

import pytest
from sqlalchemy import select

from backend.seeds import (
    DEFAULT_ROOM,
    ENEMY_DEFS,
    SECOND_ROOM,
    seed_default_rooms,
)
from backend.room_validation import validate_enemy_refs, validate_room
from backend.models import EnemyDef, Room, RoomConnection, TileType

_KNOWN_IDS = {d["id"] for d in ENEMY_DEFS}


# ---- validation: the good cases ----

def test_default_rooms_are_valid():
    validate_room(DEFAULT_ROOM)   # must not raise
    validate_room(SECOND_ROOM)
    validate_enemy_refs(DEFAULT_ROOM, _KNOWN_IDS)


def test_capacity_equals_spawn_count():
    assert Room(spawn_points=[[1, 1], [2, 2], [3, 3]]).capacity == 3
    assert Room(spawn_points=DEFAULT_ROOM["spawn_points"]).capacity == 4


# ---- validation: the rejection cases (one rule each) ----

def _broken(**overrides):
    data = copy.deepcopy(DEFAULT_ROOM)
    data.update(overrides)
    return data


def test_rejects_missing_field():
    data = copy.deepcopy(DEFAULT_ROOM)
    del data["spawn_points"]
    with pytest.raises(ValueError, match="spawn_points"):
        validate_room(data)


def test_rejects_wrong_row_width():
    bad = _broken(terrain=DEFAULT_ROOM["terrain"][:-1] + ["#####"])
    with pytest.raises(ValueError, match="chars wide"):
        validate_room(bad)


def test_rejects_wrong_row_count():
    with pytest.raises(ValueError, match="rows"):
        validate_room(_broken(terrain=DEFAULT_ROOM["terrain"][:5]))


def test_rejects_unknown_tile():
    rows = list(DEFAULT_ROOM["terrain"])
    rows[1] = "#..X.....#"
    with pytest.raises(ValueError, match="unknown tile"):
        validate_room(_broken(terrain=rows))


def test_rejects_spawn_on_wall():
    with pytest.raises(ValueError, match="not walkable"):
        validate_room(_broken(spawn_points=[[0, 0]]))   # (0,0) is a wall


def test_rejects_spawn_far_from_entry():
    # (1,5) is valid open floor but nowhere near a door/portal.
    with pytest.raises(ValueError, match="entry"):
        validate_room(_broken(spawn_points=[[1, 5]]))


def test_rejects_enemy_without_id():
    bad = _broken(enemy_spawns=[{"x": 4, "y": 3}])
    with pytest.raises(ValueError, match="enemy_id"):
        validate_room(bad)


def test_rejects_out_of_bounds_enemy():
    with pytest.raises(ValueError, match="out of bounds"):
        validate_room(_broken(enemy_spawns=[{"enemy_id": 1, "x": 99, "y": 99}]))


def test_rejects_overlap():
    with pytest.raises(ValueError, match="overlaps"):
        validate_room(_broken(spawn_points=[[4, 8], [4, 8]]))


def test_rejects_chest_with_designed_loot():
    # Inverted with the loot system (docs/LOOT.md): contents are rolled at
    # open time, so a chest carrying a designed "loot" list is the error now.
    with pytest.raises(ValueError, match="loot"):
        validate_room(_broken(objects=[{"type": "chest", "x": 1, "y": 1, "loot": ["coin"]}]))


def test_rejects_unknown_enemy_ref():
    with pytest.raises(ValueError, match="not a known enemy"):
        validate_enemy_refs(_broken(enemy_spawns=[{"enemy_id": 999, "x": 4, "y": 3}]), _KNOWN_IDS)


def test_rejects_sealed_off_tile():
    # Box spawn (4,8) into a 2-tile pocket with the entry door, sealed off from
    # the rest — the far door (4,0) becomes unreachable.
    rows = list(DEFAULT_ROOM["terrain"])
    rows[7] = "#...#....#"   # wall at (4,7)
    rows[8] = "#..#.#...#"   # walls at (3,8) and (5,8)
    with pytest.raises(ValueError, match="walled off"):
        validate_room(_broken(terrain=rows, spawn_points=[[4, 8]],
                               enemy_spawns=[], objects=[]))


# ---- persistence: store + read back, normalized enemies, graph link ----

async def test_seed_round_trip(session):
    await seed_default_rooms(session)

    room = (await session.execute(
        select(Room).where(Room.name == "The Pillared Hall"))).scalar_one()

    assert room.terrain == DEFAULT_ROOM["terrain"]
    assert room.objects == DEFAULT_ROOM["objects"]
    assert room.spawn_points == DEFAULT_ROOM["spawn_points"]
    assert room.enemy_spawns == DEFAULT_ROOM["enemy_spawns"]
    assert room.capacity == 4


async def test_enemy_stats_loaded_from_db(session):
    """The room stores only {enemy_id,x,y}; the stats come from enemy_defs."""
    room = await seed_default_rooms(session)

    placement = room.enemy_spawns[0]              # {"enemy_id": 1, "x": 4, "y": 3}
    assert set(placement) == {"enemy_id", "x", "y"}   # no stats duplicated in the room

    goblin = await session.get(EnemyDef, placement["enemy_id"])
    assert goblin.name == "Goblin"
    assert (goblin.hp, goblin.attack_damage, goblin.defense) == (6, 1, 1)
    # on_death is an empty hook until a system reads it — the future "enemies
    # drop loot" seam will call loot.spawn_loot, not carry item lists here.
    assert goblin.on_death == []


async def test_door_links_to_second_room(session):
    default = await seed_default_rooms(session)

    conn = (await session.execute(
        select(RoomConnection).where(
            RoomConnection.from_room_id == default.id,
            RoomConnection.from_x == 4, RoomConnection.from_y == 9,
        ))).scalar_one()
    assert TileType(default.terrain[conn.from_y][conn.from_x]) is TileType.DOOR

    target = await session.get(Room, conn.to_room_id)
    assert target.name == "The Antechamber"
