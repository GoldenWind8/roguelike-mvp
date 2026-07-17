"""The preset registry + the one entry point everything calls: `generate`.

Room types are DATA here (a dict of presets), the way modes and brains are
singletons chosen from data elsewhere in the backend. Each preset pairs a
different industry technique with the SAME contract — same param schema
machinery, same real-validator gate, same seed determinism — which is exactly
what lets an AI config-picker later choose any of them safely.
"""
import random

from backend.procgen import cave, dungeon, house, keep, overworld, wfc
from backend.procgen.base import GenResult, Preset, validate

# How many times to re-roll (new seed) if a generator emits an invalid room.
# Constructive presets (dungeon, castle) should pass on attempt 1; emergent
# ones (cave, ruin) and the damage-happy house are WHY this net exists.
_MAX_ATTEMPTS = 8


REGISTRY: dict[str, Preset] = {
    "dungeon": Preset(
        key="dungeon",
        label="Dungeon Hall",
        technique="Template + constructive connectivity (Spelunky family)",
        description="Walled hall with doors and pillar obstacles. Connected by "
                    "construction — the clear perimeter ring touches every door.",
        generator=dungeon.generate,
        params=dungeon.PARAMS,
    ),
    "castle": Preset(
        key="castle",
        label="Castle Keep",
        technique="Binary Space Partitioning (Rogue lineage)",
        description="Chambers and corridors from a recursive floor-plan split; "
                    "one corridor per tree node connects the whole keep.",
        generator=keep.generate,
        params=keep.PARAMS,
    ),
    "cave": Preset(
        key="cave",
        label="Cave",
        technique="Cellular automata (classic roguelike caves)",
        description="Random noise smoothed into organic caverns; the largest "
                    "region is kept and cave mouths are punched into it.",
        generator=cave.generate,
        params=cave.PARAMS,
    ),
    "broken_house": Preset(
        key="broken_house",
        label="Broken House",
        technique="Prefab stitching + damage pass (authored chunks)",
        description="A hand-drawn floor plan stamped into a fenced yard, then "
                    "procedurally ruined: breaches, crumbled walls, rubble.",
        generator=house.generate,
        params=house.PARAMS,
    ),
    "open_land": Preset(
        key="open_land",
        label="Open Land",
        technique="Fractal value noise (Minecraft/Terraria family)",
        description="A wilderness block from thresholded noise, with carved "
                    "trails, edge exits, and portal POIs leading into dungeons.",
        generator=overworld.generate,
        params=overworld.PARAMS,
    ),
    "ruin": Preset(
        key="ruin",
        label="Ancient Ruin",
        technique="Wave Function Collapse (Bad North/Townscaper family)",
        description="Constraint-solved masonry: adjacency laws learned from a "
                    "hand-drawn sample, then collapsed cell by cell.",
        generator=wfc.generate,
        params=wfc.PARAMS,
    ),
}


def list_types() -> list[dict]:
    """Preset metadata for a UI dropdown or an AI's menu of choices."""
    return [{"key": p.key, "label": p.label, "description": p.description,
             "technique": p.technique}
            for p in REGISTRY.values()]


def schema_for(room_type: str) -> list[dict]:
    """The param schema for one type — drives the harness form AND bounds the
    values an AI config-picker may later emit."""
    preset = REGISTRY.get(room_type)
    if preset is None:
        raise KeyError(f"unknown room type '{room_type}' — have {list(REGISTRY)}")
    return [p.to_json() for p in preset.params]


def generate(room_type: str, params: dict | None = None, seed: int = 0) -> GenResult:
    """Generate one room of `room_type`. Coerces params into the schema's
    domain, seeds the RNG for reproducibility (same seed + params -> identical
    room), and validates through the REAL game gate. Re-rolls with a bumped
    seed on the rare invalid room; returns the validator's message if every
    attempt fails."""
    preset = REGISTRY.get(room_type)
    if preset is None:
        raise KeyError(f"unknown room type '{room_type}' — have {list(REGISTRY)}")

    coerced = preset.coerce_params(params)
    last_error = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        rng = random.Random(seed + attempt - 1)
        room = preset.generator(coerced, rng)
        last_error = validate(room)
        if last_error is None:
            return GenResult(ok=True, room=room, seed=seed, attempts=attempt,
                             room_type=room_type, params=coerced)
    return GenResult(ok=False, room=None, seed=seed, attempts=_MAX_ATTEMPTS,
                     room_type=room_type, params=coerced, error=last_error)
