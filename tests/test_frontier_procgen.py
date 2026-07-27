import pytest

from backend.procgen import generate
from backend.procgen.base import validate
from backend.procgen.frontier import (
    DiscoveryPolicy,
    FrontierPressure,
    RegionCandidate,
    authored_region_chance,
    frontier_recipe,
    resolve_frontier_exit,
)
from backend.procgen.frontier_wilds import ARCHETYPES


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_every_frontier_archetype_survives_extreme_content_sweep(archetype):
    for seed in range(12):
        result = generate(
            "frontier_wilds",
            {
                "archetype": archetype,
                "width": 42,
                "height": 30,
                "entries": 4,
                "secrets": 3,
                "capacity": 6,
                "enemies": 14,
                "chests": 4,
                "barrels": 3,
            },
            seed,
        )
        assert result.ok, f"{archetype}/seed{seed}: {result.error}"
        assert validate(result.room) is None
        assert sum(row.count("+") for row in result.room["terrain"]) == 4
        assert sum(row.count("O") for row in result.room["terrain"]) <= 3


@pytest.mark.parametrize("archetype", ARCHETYPES)
@pytest.mark.parametrize("size", ((20, 14), (30, 20), (42, 30)))
def test_frontier_archetypes_are_valid_across_size_range(archetype, size):
    result = generate(
        "frontier_wilds",
        {
            "archetype": archetype,
            "width": size[0],
            "height": size[1],
            "entries": 3,
            "secrets": 1,
        },
        919,
    )
    assert result.ok, result.error


def test_frontier_generation_is_reproducible_but_visually_varied():
    first = generate(
        "frontier_wilds", {"archetype": "rotwood"}, 77
    )
    replay = generate(
        "frontier_wilds", {"archetype": "rotwood"}, 77
    )
    other = generate(
        "frontier_wilds", {"archetype": "rotwood"}, 78
    )
    assert first.room == replay.room
    assert first.room != other.room


def test_region_discovery_chance_rises_and_has_hard_pity():
    policy = DiscoveryPolicy()
    chances = [
        authored_region_chance(
            pressure=FrontierPressure(misses=misses),
            depth=5,
            policy=policy,
        )
        for misses in range(policy.hard_pity_after + 1)
    ]
    assert chances == sorted(chances)
    assert chances[0] < chances[5] < chances[12]
    assert chances[-1] == 1


def test_frontier_exit_is_stable_and_pressure_changes_with_outcome():
    candidates = (
        RegionCandidate("veyr", "Veyr", min_depth=3),
        RegionCandidate(
            "drazna",
            "Drazna",
            min_depth=5,
            required_fact="heard_of_drazna",
        ),
    )
    kwargs = {
        "world_seed": 44,
        "exit_key": "room-9:east",
        "depth": 7,
        "pressure": FrontierPressure(misses=2),
        "candidates": candidates,
        "known_facts": frozenset({"heard_of_drazna"}),
    }
    first = resolve_frontier_exit(**kwargs)
    replay = resolve_frontier_exit(**kwargs)
    assert first == replay
    if first.kind == "generated_room":
        assert first.next_pressure.misses == 3
        assert first.region_id is None
    else:
        assert first.next_pressure.misses == 0
        assert first.region_id in {"veyr", "drazna"}


def test_hard_pity_connects_to_an_eligible_authored_region():
    outcome = resolve_frontier_exit(
        world_seed=1,
        exit_key="pity-exit",
        depth=20,
        pressure=FrontierPressure(misses=18),
        candidates=(RegionCandidate("veyr", "Veyr", min_depth=3),),
    )
    assert outcome.kind == "authored_region"
    assert outcome.region_id == "veyr"
    assert outcome.chance == 1
    assert outcome.next_pressure.misses == 0


def test_ineligible_regions_never_short_circuit_the_generated_frontier():
    outcome = resolve_frontier_exit(
        world_seed=1,
        exit_key="too-shallow",
        depth=1,
        pressure=FrontierPressure(misses=999),
        candidates=(RegionCandidate("veyr", "Veyr", min_depth=3),),
    )
    assert outcome.kind == "generated_room"
    assert outcome.chance == 0
    assert outcome.next_pressure.misses == 1000


@pytest.mark.parametrize(
    "biome",
    (
        "amberfall_fields",
        "veyr_approach",
        "drazna_marches",
        "rouvray_lowlands",
        "deep_frontier",
    ),
)
def test_biome_recipe_is_stable_bounded_and_generates_a_real_room(biome):
    recipe = frontier_recipe(
        world_seed=2026,
        node_key=f"{biome}:13",
        depth=13,
        biome=biome,
    )
    replay = frontier_recipe(
        world_seed=2026,
        node_key=f"{biome}:13",
        depth=13,
        biome=biome,
    )
    assert recipe == replay
    assert recipe.preset == "frontier_wilds"
    assert 2 <= recipe.params["entries"] <= 4
    assert 0 <= recipe.params["enemies"] <= 14
    result = generate(
        recipe.preset,
        recipe.params,
        seed=2026 + 13,
    )
    assert result.ok, result.error
