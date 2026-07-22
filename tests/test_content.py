import copy

import pytest

from backend.actor_defs import enemy_art, get_actor_art
from backend.content import load_catalog, load_json
from backend.object_defs import get_object_definition
from backend.room_loader import _object_payload
from backend.room_validation import validate_room
from backend.seeds import ENEMY_DEFS, OAKRUN_ROOM, OAKRUN_NPC_SEEDS


def test_authored_catalogues_are_runtime_sources():
    assert get_actor_art("basil").image == "/art/world/actors/basil-world-v1.png"
    assert enemy_art("Road Bandit").image == "/art/world/enemies/road-bandit-v1.png"
    assert get_object_definition("stone_well").visual_size == (2, 3)
    assert get_object_definition("wayfarers_rest_exterior").footprint[-1] == (4, 1)
    assert {enemy["id"] for enemy in ENEMY_DEFS} == set(range(1, 13))


def test_oakrun_world_and_persistent_npcs_load_from_json():
    world = load_json("world/oakrun.json")
    npcs = load_catalog("npcs.json")

    assert OAKRUN_ROOM is world["rooms"]["oakrun"] or OAKRUN_ROOM == world["rooms"]["oakrun"]
    assert OAKRUN_ROOM["id"] == "oakrun_crossroads"
    assert len(OAKRUN_NPC_SEEDS) == len(npcs) == 7


def test_authored_object_placement_ids_are_stable_and_unique():
    ids = [obj["id"] for obj in OAKRUN_ROOM["objects"]]
    assert len(ids) == len(set(ids))
    first = _object_payload(OAKRUN_ROOM["objects"][0], 99)
    assert first.id == "oakrun_wayfarers_rest"

    broken = copy.deepcopy(OAKRUN_ROOM)
    broken["objects"][1]["id"] = broken["objects"][0]["id"]
    with pytest.raises(ValueError, match="duplicate object placement id"):
        validate_room(broken)


def test_runtime_catalogues_have_no_asset_lifecycle_states():
    for filename in ("actors.json", "enemies.json", "objects.json", "buildings.json"):
        for entry in load_json(filename):
            assert "status" not in entry
