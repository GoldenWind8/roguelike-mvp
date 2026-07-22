"""Organic cavern.

Technique: CELLULAR AUTOMATA — the classic cave algorithm (RogueBasin's
best-known recipe; the same family Terraria and countless roguelikes use for
organic spaces). It's the opposite philosophy to the dungeon template:

  1. Seed the interior with random noise (`wall_chance`% wall).
  2. Run a few smoothing steps: each cell looks at its 8 neighbours and becomes
     wall if 5+ of them are wall, floor otherwise. Noise self-organizes into
     blobby, natural-looking caverns — structure EMERGES instead of being drawn.
  3. Emergent shape comes with no guarantees, so we impose them afterwards:
     keep the largest connected region (wall-fill the pockets), punch cave
     mouths in the border, then decorate only proven-reachable tiles.

This is the generate-and-test half of the craft: the automaton can roll a
degenerate map (all wall, or a cavern too cramped for the spawn cluster), and
when it does, the registry's retry net re-rolls with a bumped seed. Watch the
"attempt N" readout in the harness — this preset is why it exists.
"""
import random

from backend.procgen.base import Param
from backend.procgen.geometry import (
    FLOOR, WALL, flood_floor, interior_neighbour, keep_largest_region,
    pick_spawns, populate_contents, punch_border_entries,
)

PARAMS = (
    Param("width", "int", "Width", 20, min=12, max=32),
    Param("height", "int", "Height", 14, min=10, max=24),
    Param("wall_chance", "int", "Initial wall noise %", 44, min=30, max=55,
          help="Seed density before smoothing. ~45 gives classic caves; higher = tighter tunnels."),
    Param("smooth_steps", "int", "Smoothing steps", 4, min=0, max=6,
          help="Automaton iterations. 0 = raw noise, 4-5 = smooth caverns."),
    Param("mouths", "int", "Cave mouths", 2, min=1, max=3,
          help="Entries punched into the border, spread across sides."),
    Param("capacity", "int", "Player spawns", 4, min=1, max=6),
    Param("enemies", "int", "Enemies", 5, min=0, max=10),
    Param("chests", "int", "Chests", 1, min=0, max=4),
    Param("barrels", "int", "Fire barrels", 0, min=0, max=3),
)


def generate(params: dict, rng: random.Random) -> dict:
    w, h = params["width"], params["height"]

    # 1. Random noise inside a solid border.
    grid = [[WALL] * w for _ in range(h)]
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            grid[y][x] = WALL if rng.randint(1, 100) <= params["wall_chance"] else FLOOR

    # 2. Smooth: the 5-of-8 neighbour rule, applied simultaneously (read from
    #    the old grid, write a new one — sequential updates smear directionally).
    for _ in range(params["smooth_steps"]):
        grid = _smooth(grid, w, h)

    # 3. Impose the guarantees emergence doesn't give us.
    region = keep_largest_region(grid, w, h)
    mouths = punch_border_entries(grid, w, h, params["mouths"], rng)
    entry = interior_neighbour(mouths[0], w, h)

    reachable = flood_floor(grid, w, h, entry) or region
    occupied: set[tuple[int, int]] = set()
    spawns = pick_spawns(mouths, reachable, params["capacity"], occupied)
    enemy_spawns, objects = populate_contents(reachable, occupied, params, rng, mouths)

    return {
        "name": _name(rng),
        "width": w,
        "height": h,
        "terrain": ["".join(row) for row in grid],
        "spawn_points": [[x, y] for (x, y) in spawns],
        "enemy_spawns": enemy_spawns,
        "objects": objects,
    }


def _smooth(grid, w, h) -> list[list[str]]:
    """One automaton step. Out-of-bounds counts as wall, which makes the cave
    hug its border and keeps edges solid."""
    out = [[WALL] * w for _ in range(h)]
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            walls = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < w and 0 <= ny < h) or grid[ny][nx] == WALL:
                        walls += 1
            out[y][x] = WALL if walls >= 5 else FLOOR
    return out


_ADJ = ("Dripping", "Echoing", "Sunken", "Glimmering", "Black", "Howling")
_NOUN = ("Hollow", "Grotto", "Cavern", "Warren", "Depths", "Burrow")


def _name(rng) -> str:
    return f"The {rng.choice(_ADJ)} {rng.choice(_NOUN)}"
