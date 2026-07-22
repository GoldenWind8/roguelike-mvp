"""Open land — a block of the overworld.

Technique: FRACTAL VALUE NOISE — the Minecraft/Terraria family. Random values
on a coarse lattice, smoothly interpolated, plus a finer octave at lower
weight: cheap, seedable, and it produces natural-looking terrain because
nearby tiles get similar values (thickets and rock CLUMP instead of speckling).
Threshold the noise field and you have wilderness.

Noise gives zero guarantees, so the walkable skeleton is carved on top,
constructively — this is how open-world games do it too (the biome comes from
noise; roads, rivers and POIs are placed and carved deliberately):

  - path exits at the map edges (doors leading to neighbouring land blocks),
  - portal POIs (a castle gate, a cave mouth — each takes the player INTO a
    generated dungeon room; this is your overworld-to-dungeon seam),
  - winding trails connecting entry -> every POI -> every other exit, so all
    entries and portals are reachable BY CONSTRUCTION.
"""
import random

from backend.procgen.base import Param
from backend.procgen.geometry import (
    DOOR, FLOOR, PORTAL, WALL, flood_floor, interior_neighbour, pick_spawns,
    populate_contents,
)

# Terrain flavor -> noise threshold: what fraction of the land is blocked
# (trees/rock). The noise SHAPE is the same; only the cut line moves.
_DENSITY = {"meadow": 0.34, "forest": 0.46, "rocky": 0.54}

PARAMS = (
    Param("width", "int", "Width", 28, min=20, max=36),
    Param("height", "int", "Height", 18, min=14, max=28),
    Param("terrain", "choice", "Terrain", "forest",
          options=("meadow", "forest", "rocky"),
          help="How choked the land is. Same noise, different threshold."),
    Param("paths", "int", "Path exits", 2, min=1, max=4,
          help="Edge doors leading to neighbouring overworld blocks."),
    Param("pois", "int", "Portal POIs", 1, min=0, max=3,
          help="Castle gates / cave mouths — portals into dungeon rooms."),
    Param("capacity", "int", "Player spawns", 4, min=1, max=6),
    Param("enemies", "int", "Enemies", 4, min=0, max=10),
    Param("chests", "int", "Chests", 1, min=0, max=3),
)


def generate(params: dict, rng: random.Random) -> dict:
    w, h = params["width"], params["height"]

    # 1. Noise field -> wilderness. Border stays solid (the block's edge).
    noise = _fractal_noise(w, h, rng)
    threshold = _DENSITY[params["terrain"]]
    grid = [[WALL if (x in (0, w - 1) or y in (0, h - 1) or noise[y][x] < threshold)
             else FLOOR for x in range(w)] for y in range(h)]

    # 2. Path exits on distinct edges, then POI portals spread across the middle.
    exits = _edge_doors(grid, w, h, params["paths"], rng)
    pois = _place_pois(grid, w, h, params["pois"], rng)

    # 3. Carve winding trails: entry -> each POI -> each other exit. After this,
    #    every door and portal is connected by construction.
    entry = interior_neighbour(exits[0], w, h)
    grid[entry[1]][entry[0]] = FLOOR
    for target in pois + [interior_neighbour(d, w, h) for d in exits[1:]]:
        _carve_trail(grid, w, h, entry, target, rng)

    reachable = flood_floor(grid, w, h, entry)
    occupied: set[tuple[int, int]] = set()
    spawns = pick_spawns(exits + pois, reachable, params["capacity"], occupied)
    enemy_spawns, objects = populate_contents(reachable, occupied, params, rng, exits + pois)

    return {
        "name": _name(params["terrain"], rng),
        "width": w,
        "height": h,
        "terrain": ["".join(row) for row in grid],
        "spawn_points": [[x, y] for (x, y) in spawns],
        "enemy_spawns": enemy_spawns,
        "objects": objects,
    }


def _fractal_noise(w, h, rng) -> list[list[float]]:
    """Two octaves of value noise in [0, 1): a coarse lattice for the big
    landmasses plus a finer one, quarter-weighted, for raggedy edges."""
    base = _value_noise(w, h, cell=5, rng=rng)
    detail = _value_noise(w, h, cell=2, rng=rng)
    return [[0.75 * base[y][x] + 0.25 * detail[y][x] for x in range(w)]
            for y in range(h)]


def _value_noise(w, h, cell, rng) -> list[list[float]]:
    """Random values at every `cell`-th lattice point, bilinearly interpolated
    between them — the simplest member of the noise family (Perlin/Simplex are
    its gradient-based cousins; same idea, fewer directional artifacts)."""
    gw, gh = w // cell + 2, h // cell + 2
    lattice = [[rng.random() for _ in range(gw)] for _ in range(gh)]
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        gy, fy = divmod(y, cell)
        ty = fy / cell
        for x in range(w):
            gx, fx = divmod(x, cell)
            tx = fx / cell
            top = lattice[gy][gx] * (1 - tx) + lattice[gy][gx + 1] * tx
            bot = lattice[gy + 1][gx] * (1 - tx) + lattice[gy + 1][gx + 1] * tx
            out[y][x] = top * (1 - ty) + bot * ty
    return out


def _edge_doors(grid, w, h, count, rng) -> list[tuple[int, int]]:
    """A door per chosen edge at a jittered midpoint — where a path leaves this
    block for the next one."""
    doors = []
    for side in rng.sample(["S", "N", "E", "W"], count):
        if side in ("N", "S"):
            doors.append((rng.randint(w // 4, 3 * w // 4), 0 if side == "N" else h - 1))
        else:
            doors.append((0 if side == "W" else w - 1, rng.randint(h // 4, 3 * h // 4)))
    for x, y in doors:
        grid[y][x] = DOOR
    return doors


def _place_pois(grid, w, h, count, rng) -> list[tuple[int, int]]:
    """Portal tiles in the middle band, kept apart from each other. Each is a
    doorway into a dungeon — where the overworld hands off to another preset."""
    pois: list[tuple[int, int]] = []
    tries = 0
    while len(pois) < count and tries < 200:
        tries += 1
        x = rng.randint(w // 5, 4 * w // 5)
        y = rng.randint(h // 5, 4 * h // 5)
        if all(abs(x - px) + abs(y - py) >= max(w, h) // 3 for px, py in pois):
            pois.append((x, y))
    for x, y in pois:
        grid[y][x] = PORTAL
    return pois


def _carve_trail(grid, w, h, frm, to, rng) -> None:
    """A drunk-but-determined walk: usually steps toward the target, sometimes
    sideways, flooring everything it crosses (but never a door/portal tile).
    Wobble is what makes it read as a trail instead of a surveyor's line."""
    x, y = frm
    while (x, y) != to:
        dx = (to[0] > x) - (to[0] < x)
        dy = (to[1] > y) - (to[1] < y)
        options = []
        if dx:
            options += [(dx, 0)] * 3
        if dy:
            options += [(0, dy)] * 3
        options += [(0, 1), (0, -1), (1, 0), (-1, 0)]      # the wobble
        sx, sy = rng.choice(options)
        nx, ny = x + sx, y + sy
        if not (0 < nx < w - 1 and 0 < ny < h - 1) and (nx, ny) != to:
            continue
        x, y = nx, ny
        if grid[y][x] == WALL:
            grid[y][x] = FLOOR


_ADJ = {"meadow": ("Sunlit", "Wide", "Quiet"), "forest": ("Bramble", "Deep", "Whispering"),
        "rocky": ("Shattered", "Windswept", "Grey")}
_NOUN = ("Reach", "Waste", "Expanse", "Crossing", "Fields", "Wilds")


def _name(terrain, rng) -> str:
    return f"The {rng.choice(_ADJ[terrain])} {rng.choice(_NOUN)}"
