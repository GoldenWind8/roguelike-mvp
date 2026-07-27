from backend.content import load_region
from backend.models import TileType


def test_oakrun_frontier_exit_to_drazna_is_a_player_enterable_tile():
    """The procedural road must be reachable through play, not only store APIs."""
    fieldsite = load_region("world/oakrun/region.json")["rooms"][
        "oakrun_fieldsite_verge"
    ]

    assert fieldsite["frontier_exits"] == [{
        "x": 16,
        "y": 6,
        "biome_hint": "amberfall_fields",
        "label": "The road beyond the severed maps",
    }]
    exit_definition = fieldsite["frontier_exits"][0]
    exit_tile = TileType(
        fieldsite["terrain"][exit_definition["y"]][exit_definition["x"]]
    )

    assert exit_tile in {TileType.DOOR, TileType.PORTAL}
