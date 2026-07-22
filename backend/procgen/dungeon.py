"""Walled dungeon hall — the first generator.

Technique: TEMPLATE + CONSTRUCTIVE CONNECTIVITY — the Spelunky/Isaac family.
We don't scatter tiles and pray they're solvable; we build a shape that is
connected BY CONSTRUCTION, then populate contents only onto tiles we've PROVEN
reachable. The validator (`validate_room`) then just confirms what we already
guaranteed — so a valid room comes out on the first roll, not the fiftieth.

Shape, in order:
  1. Solid wall border, floor interior (the "template").
  2. Punch doors on chosen sides (an entry the player arrives through).
  3. Place pillar obstacles ONLY in the inner field, never on the interior
     perimeter ring — that clear ring is a corridor touching every door, which
     is what makes connectivity free instead of a dice roll.
  4. Flood-fill the reachable floor from the entry, then draw spawns, enemies,
     and objects only from that set. Structure first, decoration second.
"""
import random

from backend.procgen.base import Param
from backend.procgen.geometry import (
    DOOR, FLOOR, WALL, flood_floor, interior_neighbour, pick_spawns,
    populate_contents,
)

# The knobs. Defaults reproduce something close to your hand-authored Pillared
# Hall (10x10, north+south doors, a regular pillar lattice) so you have a
# known-good visual target on first launch.
PARAMS = (
    Param("width", "int", "Width", 10, min=6, max=20, help="Interior + walls, in tiles."),
    Param("height", "int", "Height", 10, min=6, max=16),
    Param("doors", "choice", "Doors", "north_south",
          options=("south", "north_south", "four", "random_two"),
          help="Where the entries are. Spawns cluster at the first door."),
    Param("pillars", "choice", "Pillar style", "grid",
          options=("none", "grid", "scatter"),
          help="grid = regular lattice (castle hall); scatter = random rubble (ruin)."),
    Param("pillar_density", "int", "Scatter density %", 12, min=0, max=40,
          help="Only used when pillar style is 'scatter'."),
    Param("capacity", "int", "Player spawns", 4, min=1, max=6,
          help="Room capacity = number of spawn points."),
    Param("enemies", "int", "Enemies", 4, min=0, max=8),
    Param("chests", "int", "Chests", 2, min=0, max=4),
    Param("barrels", "int", "Fire barrels", 1, min=0, max=3),
)


def generate(params: dict, rng: random.Random) -> dict:
    w, h = params["width"], params["height"]

    # 1. Template: wall border, floor interior.
    grid = [[WALL if (x in (0, w - 1) or y in (0, h - 1)) else FLOOR
             for x in range(w)] for y in range(h)]

    # 2. Doors on the chosen sides. Each door sits at a non-corner midpoint of
    #    its wall; its interior neighbour is guaranteed floor (it's the ring).
    doors = _place_doors(grid, w, h, params["doors"], rng)
    entry = interior_neighbour(doors[0], w, h)   # players arrive at door 0

    # 3. Pillars in the inner field only (never the perimeter ring) — keeps
    #    every door on one connected corridor.
    if params["pillars"] != "none":
        _place_pillars(grid, w, h, params["pillars"], params["pillar_density"], rng)

    # 4. Prove reachability, then decorate onto proven tiles.
    reachable = flood_floor(grid, w, h, entry)
    occupied: set[tuple[int, int]] = set()
    spawns = pick_spawns(doors, reachable, params["capacity"], occupied)
    enemy_spawns, objects = populate_contents(reachable, occupied, params, rng, doors)

    return {
        "name": _name(rng),
        "width": w,
        "height": h,
        "terrain": ["".join(row) for row in grid],
        "spawn_points": [[x, y] for (x, y) in spawns],
        "enemy_spawns": enemy_spawns,
        "objects": objects,
    }


# --- shape helpers ------------------------------------------------------------


def _sides_for(mode: str, rng: random.Random) -> list[str]:
    if mode == "south":
        return ["S"]
    if mode == "north_south":
        return ["N", "S"]
    if mode == "four":
        return ["N", "S", "E", "W"]
    # random_two: two distinct sides
    return rng.sample(["N", "S", "E", "W"], 2)


def _place_doors(grid, w, h, mode, rng) -> list[tuple[int, int]]:
    """Punch a door at the (jittered) midpoint of each chosen side. Returns the
    door tiles in the order sides were chosen — door 0 is the arrival entry."""
    doors = []
    for side in _sides_for(mode, rng):
        if side in ("N", "S"):
            x = _jittered_mid(w, rng)             # non-corner column
            y = 0 if side == "N" else h - 1
        else:
            y = _jittered_mid(h, rng)             # non-corner row
            x = 0 if side == "W" else w - 1
        grid[y][x] = DOOR
        doors.append((x, y))
    return doors


def _jittered_mid(span: int, rng: random.Random) -> int:
    """A near-centre index in [1, span-2] (never a corner)."""
    mid = span // 2
    lo, hi = max(1, mid - 1), min(span - 2, mid + 1)
    return rng.randint(lo, hi)


def _place_pillars(grid, w, h, style, density, rng) -> None:
    """Obstacles in the INNER field only (2..w-3, 2..h-3) so the interior
    perimeter ring stays a clear corridor and every door remains connected."""
    for y in range(2, h - 2):
        for x in range(2, w - 2):
            if style == "grid":
                if x % 3 == 0 and y % 2 == 0:      # regular lattice, hall-like
                    grid[y][x] = WALL
            elif style == "scatter":
                if rng.randint(1, 100) <= density:  # random rubble
                    grid[y][x] = WALL


_ADJ = ("Pillared", "Ruined", "Silent", "Cold", "Forgotten", "Ashen", "Iron")
_NOUN = ("Hall", "Chamber", "Vault", "Gallery", "Crypt", "Antechamber")


def _name(rng) -> str:
    return f"The {rng.choice(_ADJ)} {rng.choice(_NOUN)}"
