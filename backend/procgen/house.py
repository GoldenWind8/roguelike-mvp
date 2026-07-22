"""Broken house in an overgrown yard.

Technique: PREFAB STITCHING + DAMAGE PASS — hand-authored chunks placed by
code, then procedurally worn down. This is Spelunky's core trick (authored
templates, procedural assembly/variation) plus the standard "ruin pass" games
use to make destruction look organic: start from the intact structure and
subtract, because damage that was ever coherent reads as a story, while noise
that never was reads as static.

  1. Pick a floor plan (a small ASCII prefab — the authored part).
  2. Stamp it into an open yard with a fenced border and a gate.
  3. Ruin it: breach whole wall segments, crumble random masonry, drop rubble
     around the yard. Damage is random, so this preset leans on the retry net:
     a cruel roll CAN rubble-off a doorway, and the validator catches it.

Contents go on proven-reachable tiles as always — loot skews toward chests,
because scavenging a ruin is the point.
"""
import random

from backend.procgen.base import Param
from backend.procgen.geometry import (
    DOOR, FLOOR, WALL, flood_floor, interior_neighbour, pick_spawns,
    populate_contents,
)

# The authored part: floor plans as ASCII art. '#' wall, '.' interior floor,
# '+' doorway. Rows may be ragged — anything outside the plan is yard.
_PLANS = {
    "cottage": (
        "#########",
        "#.......#",
        "#.......#",
        "#.......#",
        "#.......#",
        "####+####",
    ),
    # Doorways must have floor on BOTH sides (yard out, room in) or an intact
    # (ruin=0) house seals its own entry and the validator rejects it.
    "longhouse": (
        "############",
        "#.....#....#",
        "#.....#....#",
        "#.....+....#",
        "#.....#....#",
        "########+###",
    ),
    "l_house": (
        "#######",
        "#.....#",
        "#.....#####",
        "#.........#",
        "#.....#####",
        "###+###",
    ),
}

PARAMS = (
    Param("width", "int", "Width", 20, min=16, max=28),
    Param("height", "int", "Height", 14, min=12, max=20),
    Param("plan", "choice", "Floor plan", "random",
          options=("random", "cottage", "longhouse", "l_house"),
          help="The prefab to stamp — the authored half of the technique."),
    Param("ruin", "int", "Ruin %", 45, min=0, max=100,
          help="0 = intact house, 100 = barely standing."),
    Param("capacity", "int", "Player spawns", 4, min=1, max=6),
    Param("enemies", "int", "Enemies", 3, min=0, max=8),
    Param("chests", "int", "Chests", 2, min=0, max=5),
    Param("barrels", "int", "Fire barrels", 1, min=0, max=3),
)


def generate(params: dict, rng: random.Random) -> dict:
    w, h = params["width"], params["height"]

    # 1-2. Fenced yard, then stamp the chosen plan roughly centred.
    grid = [[WALL if (x in (0, w - 1) or y in (0, h - 1)) else FLOOR
             for x in range(w)] for y in range(h)]
    plan_key = params["plan"]
    if plan_key == "random":
        plan_key = rng.choice(sorted(_PLANS))
    house = _stamp(grid, w, h, _PLANS[plan_key], rng)

    # Yard gate on the south fence: the arrival entry.
    gate = (rng.randint(2, w - 3), h - 1)
    grid[gate[1]][gate[0]] = DOOR
    entry = interior_neighbour(gate, w, h)

    # 3. The damage pass.
    protected = _near_doors(grid, w, h)          # keep doorways usable
    _ruin(grid, w, h, house, params["ruin"], protected, rng)

    reachable = flood_floor(grid, w, h, entry)
    occupied: set[tuple[int, int]] = set()
    spawns = pick_spawns([gate], reachable, params["capacity"], occupied)
    enemy_spawns, objects = populate_contents(reachable, occupied, params, rng, [gate])

    return {
        "name": _name(plan_key, params["ruin"], rng),
        "width": w,
        "height": h,
        "terrain": ["".join(row) for row in grid],
        "spawn_points": [[x, y] for (x, y) in spawns],
        "enemy_spawns": enemy_spawns,
        "objects": objects,
    }


def _stamp(grid, w, h, plan, rng) -> list[tuple[int, int, str]]:
    """Copy the prefab into the yard at a jittered near-centre spot. Returns
    the stamped cells as (x, y, plan_char) for the damage pass to work on."""
    pw = max(len(row) for row in plan)
    ph = len(plan)
    ox = (w - pw) // 2 + rng.randint(-1, 1)
    oy = (h - ph) // 2 + rng.randint(-1, 1)
    ox = max(2, min(w - pw - 2, ox))             # 1+ yard tile all around
    oy = max(2, min(h - ph - 2, oy))
    cells = []
    for py, row in enumerate(plan):
        for px, ch in enumerate(row):
            grid[oy + py][ox + px] = ch
            cells.append((ox + px, oy + py, ch))
    return cells


def _near_doors(grid, w, h) -> set[tuple[int, int]]:
    """Tiles within 1 of any doorway — the damage pass must not touch these,
    or a breach-blocking rubble pile turns an entry into a lie."""
    keep = set()
    for y in range(h):
        for x in range(w):
            if grid[y][x] == DOOR:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        keep.add((x + dx, y + dy))
    return keep


def _ruin(grid, w, h, house, ruin, protected, rng) -> None:
    """Subtract from the intact structure: breach wall runs, crumble masonry,
    scatter rubble in the yard. All three scale with the one `ruin` knob."""
    walls = [(x, y) for (x, y, ch) in house if ch == "#" and (x, y) not in protected]

    # Breaches: whole 2-3 tile runs knocked out of the shell.
    rng.shuffle(walls)
    for bx, by in walls[: ruin // 30]:
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            if (bx + dx, by + dy) in set(walls):
                grid[by + dy][bx + dx] = FLOOR

    # Crumble: individual missing stones.
    for x, y in walls:
        if grid[y][x] == "#" and rng.randint(1, 400) <= ruin:
            grid[y][x] = FLOOR

    # Rubble: collapsed debris in the yard around the house (never inside, and
    # never near a door — inside, the crumbled walls already tell the story).
    house_cells = {(x, y) for (x, y, _) in house}
    near_house = {(x + dx, y + dy) for (x, y) in house_cells
                  for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)}
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if (grid[y][x] == FLOOR and (x, y) not in house_cells
                    and (x, y) in near_house and (x, y) not in protected
                    and rng.randint(1, 500) <= ruin):
                grid[y][x] = WALL


_STATE = {0: "Tidy", 20: "Weathered", 45: "Crumbling", 75: "Collapsed"}
_KIND = {"cottage": "Cottage", "longhouse": "Longhouse", "l_house": "Manor"}


def _name(plan_key, ruin, rng) -> str:
    state = [v for k, v in sorted(_STATE.items()) if ruin >= k][-1]
    return f"The {state} {_KIND[plan_key]}"
