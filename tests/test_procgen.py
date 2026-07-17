"""The generator contract: whatever a preset does inside, its output must pass
the SAME gate the game uses, be reproducible from its seed, and survive junk
input. These are the invariants that let an AI config-picker plug in safely
later — so they are tested, not assumed."""
import itertools

import pytest

from backend.procgen import generate, list_types, schema_for
from backend.procgen.base import validate
from backend.procgen.registry import REGISTRY


def test_registry_exposes_all_presets():
    keys = [t["key"] for t in list_types()]
    for expected in ("dungeon", "castle", "cave", "broken_house", "open_land", "ruin"):
        assert expected in keys
        assert schema_for(expected), f"{expected} should expose tunable params"
    for t in list_types():
        assert t["technique"], f"{t['key']} should name its industry technique"


def test_unknown_type_raises():
    with pytest.raises(KeyError):
        generate("no_such_type", {}, 0)
    with pytest.raises(KeyError):
        schema_for("no_such_type")


# A representative sweep of the whole knob space across several seeds. Every
# combination must produce a room the real validator accepts.
_W = (6, 10, 13, 20)
_H = (6, 10, 16)
_DOORS = ("south", "north_south", "four", "random_two")
_PILLARS = ("none", "grid", "scatter")


@pytest.mark.parametrize("w,h,doors,pillars", itertools.product(_W, _H, _DOORS, _PILLARS))
def test_generated_rooms_always_pass_the_real_gate(w, h, doors, pillars):
    for seed in range(4):
        res = generate("dungeon", {
            "width": w, "height": h, "doors": doors, "pillars": pillars,
            "pillar_density": 40, "capacity": 6, "enemies": 8,
            "chests": 4, "barrels": 3,
        }, seed)
        assert res.ok, f"{w}x{h}/{doors}/{pillars}/seed{seed}: {res.error}"
        assert validate(res.room) is None  # belt-and-braces: gate agrees


# The same knob-space sweep for each new technique. Emergent generators (cave,
# ruin) and the damage-happy house lean on the retry net — the assertion here
# is that the net plus the technique ALWAYS lands on a valid room.

_CAVE = itertools.product((12, 20, 32), (10, 16, 24), (30, 44, 55), (0, 2, 5))


@pytest.mark.parametrize("w,h,wall_chance,steps", _CAVE)
def test_cave_sweep(w, h, wall_chance, steps):
    for seed in range(3):
        res = generate("cave", {
            "width": w, "height": h, "wall_chance": wall_chance,
            "smooth_steps": steps, "mouths": 3, "capacity": 6, "enemies": 10,
        }, seed)
        assert res.ok, f"cave {w}x{h}/fill{wall_chance}/steps{steps}/seed{seed}: {res.error}"


_CASTLE = itertools.product((18, 24, 34), (12, 16, 24), ("small", "medium", "large"))


@pytest.mark.parametrize("w,h,chambers", _CASTLE)
def test_castle_sweep(w, h, chambers):
    for seed in range(3):
        res = generate("castle", {
            "width": w, "height": h, "chambers": chambers, "gates": 2,
            "capacity": 6, "enemies": 12, "chests": 6, "barrels": 4,
        }, seed)
        assert res.ok, f"castle {w}x{h}/{chambers}/seed{seed}: {res.error}"


_HOUSE = itertools.product(("cottage", "longhouse", "l_house"), (0, 45, 100))


@pytest.mark.parametrize("plan,ruin", _HOUSE)
def test_broken_house_sweep(plan, ruin):
    for seed in range(4):
        res = generate("broken_house", {
            "plan": plan, "ruin": ruin, "capacity": 6, "enemies": 8, "chests": 5,
        }, seed)
        assert res.ok, f"house {plan}/ruin{ruin}/seed{seed}: {res.error}"


_LAND = itertools.product(("meadow", "forest", "rocky"), (1, 4), (0, 3))


@pytest.mark.parametrize("terrain,paths,pois", _LAND)
def test_open_land_sweep(terrain, paths, pois):
    for seed in range(3):
        res = generate("open_land", {
            "terrain": terrain, "paths": paths, "pois": pois,
            "capacity": 6, "enemies": 10,
        }, seed)
        assert res.ok, f"land {terrain}/paths{paths}/pois{pois}/seed{seed}: {res.error}"


_RUIN = itertools.product(("sparse", "classic", "dense"), (12, 18, 26), (10, 20))


@pytest.mark.parametrize("density,w,h", _RUIN)
def test_ruin_sweep(density, w, h):
    for seed in range(3):
        res = generate("ruin", {
            "width": w, "height": h, "density": density, "entries": 3,
            "capacity": 6, "enemies": 10,
        }, seed)
        assert res.ok, f"ruin {density}/{w}x{h}/seed{seed}: {res.error}"


def test_open_land_portals_are_present_and_reachable():
    # The overworld-to-dungeon seam: asked-for POIs exist as portal tiles.
    res = generate("open_land", {"pois": 2, "paths": 2}, 11)
    assert res.ok
    portals = sum(row.count("O") for row in res.room["terrain"])
    assert portals == 2  # validator already proved they're reachable


# Contract invariants shared by every preset in the registry.

@pytest.mark.parametrize("key", ("dungeon", "castle", "cave", "broken_house", "open_land", "ruin"))
def test_generation_is_deterministic(key):
    a = generate(key, {}, 7)
    b = generate(key, {}, 7)
    assert a.room == b.room


@pytest.mark.parametrize("key", ("dungeon", "castle", "cave", "broken_house", "open_land", "ruin"))
def test_different_seeds_differ(key):
    a = generate(key, {}, 1)
    b = generate(key, {}, 2)
    assert a.room != b.room


@pytest.mark.parametrize("key", ("dungeon", "castle", "cave", "broken_house", "open_land", "ruin"))
def test_untrusted_params_are_coerced_not_crashed(key):
    res = generate(key, {
        "width": 9999, "height": -5, "doors": "nonsense", "terrain": 42,
        "pillars": None, "enemies": "lots", "capacity": 999, "plan": object(),
    }, 3)
    assert res.ok
    schema = {p["name"]: p for p in schema_for(key)}
    for name, value in res.params.items():
        p = schema[name]
        if p["kind"] == "int":
            assert p["min"] <= value <= p["max"], f"{key}.{name} out of bounds"
        elif p["kind"] == "choice":
            assert value in p["options"], f"{key}.{name} not a legal choice"


def test_capacity_matches_spawn_count():
    res = generate("dungeon", {"width": 12, "height": 12, "capacity": 3}, 5)
    assert len(res.room["spawn_points"]) == 3


def test_every_preset_generates_validly():
    # Guards future presets: adding one to the registry without meeting the
    # contract fails here, not in the game.
    for key in REGISTRY:
        res = generate(key, {}, 0)
        assert res.ok, f"{key} default generate failed: {res.error}"
