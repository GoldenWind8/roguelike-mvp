"""The shape/population seam (procgen/placement.py): bare geometry must still
be a valid room, the candidate menu must only offer legal tiles, and a merged
proposal must face the same real gate as everything else. These invariants are
what make an LLM populater safe to bolt on — so they're tested, not assumed."""
import pytest

from backend.procgen import generate
from backend.procgen.ai import render_map
from backend.procgen.base import validate
from backend.procgen.placement import (
    CONTENT_PARAMS, apply_placement, candidate_tiles, shape_params,
)
from backend.procgen.playground import _gate
from backend.procgen.registry import REGISTRY
from backend.seeds import DEFAULT_ROOM


def test_shape_params_zeroes_contents_only():
    p = shape_params({"width": 12, "enemies": 5, "chests": 2, "barrels": 1})
    assert p["width"] == 12
    for name in CONTENT_PARAMS:
        assert p[name] == 0


@pytest.mark.parametrize("key", sorted(REGISTRY))
def test_bare_shape_is_still_a_valid_room(key):
    # AI placement starts from this: geometry + spawns, nothing else. It must
    # already pass the gate on its own (the LLM adds flavor, not validity).
    res = generate(key, shape_params(None), 0)
    assert res.ok, f"{key} bare shape failed: {res.error}"
    assert res.room["enemy_spawns"] == []
    assert res.room["objects"] == []


@pytest.mark.parametrize("key", sorted(REGISTRY))
def test_candidates_are_free_reachable_floor(key):
    res = generate(key, None, 3)  # full room, contents included
    room = res.room
    taken = {tuple(p) for p in room["spawn_points"]}
    taken |= {(e["x"], e["y"]) for e in room["enemy_spawns"]}
    taken |= {(o["x"], o["y"]) for o in room["objects"]}
    cands = candidate_tiles(room)
    assert cands, f"{key} offered no candidate tiles"
    for x, y in cands:
        assert room["terrain"][y][x] == ".", f"{key} candidate ({x},{y}) not floor"
        assert (x, y) not in taken, f"{key} candidate ({x},{y}) already occupied"


def test_candidates_work_on_hand_authored_rooms():
    # The helpers speak the room-dict contract, not generator internals — so
    # the hand-authored seed room is just as furnishable.
    assert candidate_tiles(DEFAULT_ROOM)


def test_apply_placement_round_trips_through_the_real_gate():
    shape = generate("dungeon", shape_params(None), 1)
    cands = candidate_tiles(shape.room)
    proposal = {
        "name": "The Toll Gate",
        "enemy_spawns": [{"enemy_id": 1, "x": cands[0][0], "y": cands[0][1]}],
        "objects": [{"type": "chest", "x": cands[1][0], "y": cands[1][1], "loot": ["coin"]}],
        "notes": "a guard shaking down travellers",
    }
    room = apply_placement(shape.room, proposal)
    assert validate(room) is None
    assert room["name"] == "The Toll Gate"
    assert shape.room["enemy_spawns"] == []  # merge copies, never mutates


def test_apply_placement_skips_junk_and_lets_the_gate_judge_the_rest():
    shape = generate("dungeon", shape_params(None), 2)
    room = apply_placement(shape.room, {
        "name": "",                                   # blank rename ignored
        "enemy_spawns": ["not a dict", {"enemy_id": 1, "x": 0, "y": 0}],
        "objects": None,
    })
    assert room["name"] == shape.room["name"]
    assert len(room["enemy_spawns"]) == 1             # junk entry dropped
    assert validate(room) is not None                 # (0,0) is wall — gate refuses


def test_gate_survives_rooms_that_are_not_room_shaped():
    # Full-AI output can be arbitrarily malformed; the harness's gate wrapper
    # must return a verdict string, never raise.
    assert _gate({}) is not None
    assert _gate({"width": "wide"}) is not None
    valid = generate("dungeon", None, 0).room
    broken = {**valid, "enemy_spawns": [{"enemy_id": 1, "x": "three", "y": 1}]}
    assert isinstance(_gate(broken), str)  # TypeError inside → verdict, not crash


def test_render_map_marks_spawns_and_axes():
    res = generate("dungeon", shape_params(None), 0)
    art = render_map(res.room)
    assert "S" in art                                  # spawns overlaid
    assert art.splitlines()[0].strip().startswith("0123456789")
