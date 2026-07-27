import copy

import pytest

from backend.actor_defs import enemy_art, get_actor_art
from backend.content import load_catalog, load_json, load_region
from backend.object_defs import get_object_definition
from backend.room_loader import _object_payload
from backend.room_validation import validate_room
from backend.seeds import ENEMY_DEFS, OAKRUN_ROOM, OAKRUN_NPC_SEEDS


def test_authored_catalogues_are_runtime_sources():
    assert get_actor_art("basil").image == "/art/world/actors/basil-world-v1.png"
    assert enemy_art("Road Bandit").image == "/art/world/enemies/road-bandit-v1.png"
    assert get_object_definition("stone_well").visual_size == (2, 3)
    assert get_object_definition("wayfarers_rest_exterior").footprint[-1] == (4, 1)
    assert {enemy["id"] for enemy in ENEMY_DEFS} == set(range(1, 17))


def test_drazna_people_have_dedicated_runtime_portraits():
    expected = {
        "queen_mara_vey": "mara-vey",
        "ilya_sorn": "ilya-sorn",
        "nera_bell": "nera-bell",
        "olek_var": "olek-var",
        "pava_mirek": "pava-mirek",
        "vasko_mirek": "vasko-mirek",
        "vesna_korr": "vesna-korr",
        "alin_vey": "alin-vey",
    }
    for art_id, filename in expected.items():
        art = get_actor_art(art_id)
        assert art is not None
        assert art.image == f"/art/world/npcs/drazna/{filename}-world-v1.webp"
        assert art.visual_size == (1, 2)


def test_oakrun_world_and_persistent_npcs_load_from_json():
    world = load_region("world/oakrun/region.json")
    npcs = load_catalog("npcs.json")

    assert OAKRUN_ROOM == world["rooms"]["oakrun_crossroads"]
    assert OAKRUN_ROOM["id"] == "oakrun_crossroads"
    assert len(world["rooms"]) == 8
    assert len(OAKRUN_NPC_SEEDS) == 10
    assert len(npcs) == 16


def test_oakrun_relationships_reference_real_people():
    npcs = load_catalog("npcs.json")
    for npc in npcs.values():
        for relationship in npc.get("relationships", []):
            assert relationship["npc_id"] in npcs
            assert relationship["npc_id"] != npc["id"]


def test_oakrun_graph_has_two_authored_loops_and_no_dangling_rooms():
    world = load_region("world/oakrun/region.json")
    edges = {(edge["from"], edge["to"]) for edge in world["connections"]}
    assert {
        ("oakrun_crossroads", "oakrun_north_road"),
        ("oakrun_north_road", "oakrun_barrow_verge"),
        ("oakrun_barrow_verge", "oakrun_pilgrims_hollow"),
        ("oakrun_pilgrims_hollow", "oakrun_crossroads"),
    } <= edges
    assert {
        ("oakrun_crossroads", "oakrun_orchard_lane"),
        ("oakrun_orchard_lane", "oakrun_old_mill_yard"),
        ("oakrun_old_mill_yard", "oakrun_fieldsite_verge"),
        ("oakrun_fieldsite_verge", "oakrun_old_tollhouse"),
        ("oakrun_old_tollhouse", "oakrun_north_road"),
    } <= edges
    connected = {room for edge in edges for room in edge}
    assert connected == set(world["rooms"])


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
