"""Drazna's travel economy must remain recoverable through exploration."""

from backend.content import load_region
from backend.procgen.frontier import frontier_recipe


def test_authored_region_has_five_early_chests_and_fifteen_total():
    region = load_region("world/drazna/region.json")
    chest_counts = {
        room_id: sum(
            obj.get("type") == "chest"
            for obj in room.get("objects", [])
        )
        for room_id, room in region["rooms"].items()
    }

    assert sum(chest_counts.values()) == 15
    assert all(
        chest_counts[room_id] >= 1
        for room_id in {
            "drazna_birch_heights",
            "drazna_eel_and_ember",
            "drazna_house_of_names",
            "drazna_reed_market",
            "drazna_walking_ward",
        }
    )


def test_every_frontier_recipe_keeps_repeatable_salvage_supply():
    biomes = (
        "amberfall_fields",
        "drazna_marches",
        "rouvray_lowlands",
        "deep_frontier",
    )
    for world_seed in range(64):
        for depth in range(1, 25):
            for biome in biomes:
                recipe = frontier_recipe(
                    world_seed=world_seed,
                    node_key=f"audit:{world_seed}:{depth}:{biome}",
                    depth=depth,
                    biome=biome,
                )
                assert 1 <= recipe.params["chests"] <= 4
