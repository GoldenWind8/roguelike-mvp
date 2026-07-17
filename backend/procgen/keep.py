"""Multi-chamber castle keep.

Technique: BINARY SPACE PARTITIONING (BSP) — the Rogue lineage, still the
default answer for "building interior with rooms and corridors" (Nethack
descendants, countless indies). Where the automaton grows shape from noise,
BSP designs top-down like an architect:

  1. Recursively split the floor plan into halves until each leaf is
     chamber-sized. The splits form a binary tree.
  2. Carve one room inside each leaf, with margins, so rooms never touch.
  3. Walk back UP the tree: at every internal node, carve an L-shaped corridor
     between a room from the left subtree and one from the right. Because each
     subtree is connected by induction, one corridor per node connects the
     whole keep — connectivity by construction again, but via a tree argument
     instead of a perimeter ring.

Then the shared last mile: gates punched in the outer wall, spawns at gate 0,
contents on proven-reachable tiles.
"""
import random

from backend.procgen.base import Param
from backend.procgen.geometry import (
    FLOOR, WALL, flood_floor, interior_neighbour, pick_spawns,
    populate_contents, punch_border_entries,
)

# Leaf minimum per chamber-size choice: the smallest width/height a leaf may
# have and still be split. Bigger minimum -> fewer, larger chambers.
_LEAF_MIN = {"small": 5, "medium": 7, "large": 9}

PARAMS = (
    Param("width", "int", "Width", 24, min=18, max=34),
    Param("height", "int", "Height", 16, min=12, max=24),
    Param("chambers", "choice", "Chamber size", "medium",
          options=("small", "medium", "large"),
          help="Controls how far the plan keeps splitting: small = many cells, large = a few big halls."),
    Param("gates", "int", "Outer gates", 1, min=1, max=2,
          help="Doors punched in the outer wall. Spawns cluster at gate 0."),
    Param("capacity", "int", "Player spawns", 4, min=1, max=6),
    Param("enemies", "int", "Enemies", 6, min=0, max=12),
    Param("chests", "int", "Chests", 3, min=0, max=6),
    Param("barrels", "int", "Fire barrels", 2, min=0, max=4),
)


def generate(params: dict, rng: random.Random) -> dict:
    w, h = params["width"], params["height"]
    grid = [[WALL] * w for _ in range(h)]

    # 1-3. Split, carve rooms, connect up the tree.
    leaf_min = _LEAF_MIN[params["chambers"]]
    _build(grid, (1, 1, w - 2, h - 2), leaf_min, rng)

    # Gates in the outer wall. Rooms keep a margin off the border, so the
    # helper's carve-a-tunnel fallback is what usually digs the gate passage —
    # which reads exactly like a castle gatehouse corridor.
    gates = punch_border_entries(grid, w, h, params["gates"], rng)
    entry = interior_neighbour(gates[0], w, h)

    # A forecourt just inside each gate: arriving players need standing room,
    # and a 1-wide gate tunnel can't hold a whole spawn cluster within the
    # validator's entry radius. Constructive fix > hoping the retry net rolls
    # a room that happens to sit near the wall.
    for gx, gy in gates:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                x, y = gx + dx, gy + dy
                if 0 < x < w - 1 and 0 < y < h - 1:
                    grid[y][x] = FLOOR

    reachable = flood_floor(grid, w, h, entry)
    occupied: set[tuple[int, int]] = set()
    spawns = pick_spawns(gates, reachable, params["capacity"], occupied)
    enemy_spawns, objects = populate_contents(reachable, occupied, params, rng)

    return {
        "name": _name(rng),
        "width": w,
        "height": h,
        "terrain": ["".join(row) for row in grid],
        "spawn_points": [[x, y] for (x, y) in spawns],
        "enemy_spawns": enemy_spawns,
        "objects": objects,
    }


def _build(grid, rect, leaf_min, rng) -> tuple[int, int]:
    """Recursively partition `rect` (x, y, w, h), carve a room per leaf, and
    corridor the two halves together at each node. Returns the centre of one
    room in this subtree — the attachment point the parent corridors to."""
    x, y, rw, rh = rect
    can_v = rw >= 2 * leaf_min + 1   # room for two leaves + the split line
    can_h = rh >= 2 * leaf_min + 1

    if not can_v and not can_h:
        return _carve_room(grid, rect, rng)

    # Split the longer axis (coin-flip when both fit) so leaves stay squarish.
    vertical = can_v if not can_h else (False if not can_v else
                                        (rw > rh or (rw == rh and rng.random() < 0.5)))
    if vertical:
        cut = rng.randint(leaf_min, rw - leaf_min - 1)
        a = _build(grid, (x, y, cut, rh), leaf_min, rng)
        b = _build(grid, (x + cut + 1, y, rw - cut - 1, rh), leaf_min, rng)
    else:
        cut = rng.randint(leaf_min, rh - leaf_min - 1)
        a = _build(grid, (x, y, rw, cut), leaf_min, rng)
        b = _build(grid, (x, y + cut + 1, rw, rh - cut - 1), leaf_min, rng)

    _carve_corridor(grid, a, b, rng)
    return a if rng.random() < 0.5 else b


def _carve_room(grid, rect, rng) -> tuple[int, int]:
    """A floor rectangle inside the leaf with a 1-tile margin (so neighbouring
    rooms never merge). Returns its centre."""
    x, y, rw, rh = rect
    roomw = rng.randint(max(2, rw - 3), rw - 2)
    roomh = rng.randint(max(2, rh - 3), rh - 2)
    rx = x + rng.randint(1, rw - roomw - 1)
    ry = y + rng.randint(1, rh - roomh - 1)
    for yy in range(ry, ry + roomh):
        for xx in range(rx, rx + roomw):
            grid[yy][xx] = FLOOR
    return (rx + roomw // 2, ry + roomh // 2)


def _carve_corridor(grid, a, b, rng) -> None:
    """An L-shaped 1-wide passage between two points; the corner direction is
    random so hallways don't all bend the same way."""
    (ax, ay), (bx, by) = a, b
    if rng.random() < 0.5:
        _carve_line(grid, ax, bx, ay, horizontal=True)
        _carve_line(grid, ay, by, bx, horizontal=False)
    else:
        _carve_line(grid, ay, by, ax, horizontal=False)
        _carve_line(grid, ax, bx, by, horizontal=True)


def _carve_line(grid, c0, c1, fixed, horizontal) -> None:
    lo, hi = min(c0, c1), max(c0, c1)
    for c in range(lo, hi + 1):
        if horizontal:
            grid[fixed][c] = FLOOR
        else:
            grid[c][fixed] = FLOOR


_ADJ = ("High", "Broken", "Grey", "Winter", "Ravens'", "Old")
_NOUN = ("Keep", "Bastion", "Ward", "Citadel", "Garrison", "Donjon")


def _name(rng) -> str:
    return f"The {rng.choice(_ADJ)} {rng.choice(_NOUN)}"
