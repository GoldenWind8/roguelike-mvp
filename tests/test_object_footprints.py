import pytest

from backend.actions import Action, ActionType
from backend.room_loader import _object_payload
from backend.room_state import RoomState
from backend.room_validation import validate_npc_placement, validate_room
from backend.systems import validate_player_action


def _open_room(**overrides):
    room = {
        "name": "Object Test",
        "width": 6,
        "height": 5,
        "terrain": [
            "+.....",
            "......",
            "......",
            "......",
            "......",
        ],
        "spawn_points": [[1, 0]],
        "enemy_spawns": [],
        "objects": [],
    }
    room.update(overrides)
    return room


def test_definition_expands_collision_separately_from_art():
    carriage = _object_payload({"type": "covered_carriage", "x": 3, "y": 2}, 0)

    assert carriage.occupied_cells() == ((3, 2), (4, 2), (3, 3), (4, 3))
    assert carriage.visual_size == (4, 3)
    assert carriage.image == "/art/world/objects/covered-carriage-v1.png"
    assert carriage.distance_from(5, 3) == 1


def test_multi_tile_object_blocks_every_logical_cell(make_template):
    carriage = _object_payload({"type": "covered_carriage", "x": 3, "y": 2}, 0)
    template = make_template(
        width=7,
        height=7,
        spawn_points=[(2, 2)],
        objects=(carriage,),
    )
    room = RoomState(template, seed=1)

    for x, y in carriage.occupied_cells():
        assert room.is_valid_position(x, y) is False
        assert room.get_object_at(x, y) is carriage

    player = room.add_player("Walker")
    error = validate_player_action(
        room,
        Action(ActionType.MOVE, player.id, direction=(1, 0)),
    )
    assert error is not None
    assert error.data["reason"] == "Can't move there"


def test_validator_expands_footprint_for_bounds_and_overlap():
    with pytest.raises(ValueError, match="out of bounds"):
        validate_room(_open_room(objects=[
            {"type": "covered_carriage", "x": 5, "y": 2},
        ]))

    with pytest.raises(ValueError, match="overlaps"):
        validate_room(_open_room(
            enemy_spawns=[{"enemy_id": 1, "x": 3, "y": 2}],
            objects=[{"type": "covered_carriage", "x": 2, "y": 1}],
        ))


def test_blocking_object_cannot_seal_a_required_route():
    corridor = {
        "name": "Narrow Corridor",
        "width": 7,
        "height": 3,
        "terrain": ["##+##+#", "#.....#", "#######"],
        "spawn_points": [[1, 1]],
        "enemy_spawns": [],
        "objects": [{"type": "chest", "x": 3, "y": 1}],
    }

    with pytest.raises(ValueError, match="blocked"):
        validate_room(corridor)


def test_npc_overlap_checks_the_whole_object_footprint():
    room = _open_room(objects=[
        {"type": "covered_carriage", "x": 2, "y": 1},
    ])

    with pytest.raises(ValueError, match="overlaps object"):
        validate_npc_placement(room, 3, 2)
