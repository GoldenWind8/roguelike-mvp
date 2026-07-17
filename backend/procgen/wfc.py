"""Ancient ruin — overgrown foundations and toppled colonnades.

Technique: WAVE FUNCTION COLLAPSE (WFC), tiled model — the constraint-solving
newcomer behind Bad North and Townscaper. The other presets are told HOW to
build (carve a ring, smooth noise, split a tree); WFC is only told WHAT IS
LEGAL, and builds anything that breaks no rule:

  1. LEARN: scan a small hand-authored sample and record, for every tile kind,
     which kinds ever appear beside it in each direction. Those observed pairs
     become the rules. (The sample uses directional wall tiles — `=` walls must
     continue horizontally, `|` vertically, ending in `#` posts — so "walls
     come in straight runs" is a learned law, not a hope.)
  2. COLLAPSE: every map cell starts as "could be anything". Repeatedly take
     the most-decided undecided cell, commit it (weighted by how common each
     tile was in the sample), and PROPAGATE: neighbours drop now-impossible
     options, their neighbours drop options in turn, and one choice ripples
     outward as structure.
  3. A collapse can paint itself into a corner (a cell with zero legal options
     left) — the classic WFC contradiction. We restart up to a dozen times,
     and the registry's retry net backs that up from outside.

Then the shared last mile: one region, punched entries, decorated reachables.
"""
import random
from collections import Counter, defaultdict

from backend.procgen.base import Param
from backend.procgen.geometry import (
    FLOOR, WALL, flood_floor, interior_neighbour, keep_largest_region,
    pick_spawns, populate_contents, punch_border_entries,
)

# The authored sample WFC learns its laws from. Alphabet: '.' open ground,
# 'o' free-standing column, '=' / '|' wall runs, '#' wall posts/corners.
# Edit this drawing and the generator's whole grammar changes with it.
_SAMPLE = (
    "..............",
    ".o..o..#===#..",
    ".......|...|..",
    ".#===#.|...|..",
    ".|...|.#===#..",
    ".|...|........",
    ".#===#..o..o..",
    "..............",
    "....#=====#...",
    "..o.|.....|...",
    "....#=====#...",
    ".o..........o.",
    "..............",
)
_TO_GAME = {".": FLOOR, "o": WALL, "=": WALL, "|": WALL, "#": WALL}
_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))

# How much to favour masonry over open ground when collapsing.
_DENSITY = {"sparse": 0.5, "classic": 1.0, "dense": 2.0}

_RESTARTS = 12   # contradiction budget per generate() call

PARAMS = (
    Param("width", "int", "Width", 18, min=12, max=26),
    Param("height", "int", "Height", 14, min=10, max=20),
    Param("density", "choice", "Masonry density", "classic",
          options=("sparse", "classic", "dense"),
          help="Scales the learned wall/column weights — same laws, more or less stone."),
    Param("entries", "int", "Entries", 2, min=1, max=3),
    Param("capacity", "int", "Player spawns", 4, min=1, max=6),
    Param("enemies", "int", "Enemies", 4, min=0, max=10),
    Param("chests", "int", "Chests", 2, min=0, max=4),
    Param("barrels", "int", "Fire barrels", 1, min=0, max=3),
)


def _learn(sample):
    """Extract the rulebook: tile frequencies + which tiles were ever observed
    next to which, per direction. This IS the whole 'algorithm design' step —
    the sample is the spec."""
    weights: Counter = Counter()
    allowed: dict = {d: defaultdict(set) for d in _DIRS}
    h, w = len(sample), len(sample[0])
    for y in range(h):
        for x in range(w):
            weights[sample[y][x]] += 1
            for dx, dy in _DIRS:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    allowed[(dx, dy)][sample[y][x]].add(sample[ny][nx])
    return tuple(sorted(weights)), weights, allowed


_TILES, _WEIGHTS, _ALLOWED = _learn(_SAMPLE)


def generate(params: dict, rng: random.Random) -> dict:
    w, h = params["width"], params["height"]
    scale = _DENSITY[params["density"]]
    weights = {t: _WEIGHTS[t] * (1.0 if t == "." else scale) for t in _TILES}

    # 2-3. Collapse the interior; restart on contradiction.
    cells = None
    for _ in range(_RESTARTS):
        cells = _collapse(w - 2, h - 2, weights, rng)
        if cells is not None:
            break
    if cells is None:   # out of restarts — an open field is still a valid ruin
        cells = {(x, y): "." for y in range(h - 2) for x in range(w - 2)}

    grid = [[WALL] * w for _ in range(h)]
    for (x, y), t in cells.items():
        grid[y + 1][x + 1] = _TO_GAME[t]

    # The shared last mile.
    keep_largest_region(grid, w, h)
    entries = punch_border_entries(grid, w, h, params["entries"], rng)
    entry = interior_neighbour(entries[0], w, h)

    reachable = flood_floor(grid, w, h, entry)
    occupied: set[tuple[int, int]] = set()
    spawns = pick_spawns(entries, reachable, params["capacity"], occupied)
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


def _collapse(w, h, weights, rng) -> dict | None:
    """One WFC run over a w x h field. Returns {(x, y): tile} on success or
    None on contradiction (some cell ran out of legal options)."""
    domains = {(x, y): set(_TILES) for y in range(h) for x in range(w)}

    def propagate(start) -> bool:
        """Ripple a domain change outward until the field is consistent again.
        False = a domain emptied (contradiction)."""
        queue = [start]
        while queue:
            cx, cy = queue.pop()
            for dx, dy in _DIRS:
                nb = (cx + dx, cy + dy)
                if nb not in domains:
                    continue
                supported = {t for t in domains[nb]
                             if any(t in _ALLOWED[(dx, dy)][s] for s in domains[(cx, cy)])}
                if supported != domains[nb]:
                    if not supported:
                        return False
                    domains[nb] = supported
                    queue.append(nb)
        return True

    undecided = {c for c, d in domains.items() if len(d) > 1}
    while undecided:
        # Min-entropy: commit where fewest options remain (rng breaks ties),
        # so decisions happen where the constraints are already tightest.
        fewest = min(len(domains[c]) for c in undecided)
        cell = rng.choice(sorted(c for c in undecided if len(domains[c]) == fewest))
        options = sorted(domains[cell])
        pick = rng.choices(options, [weights[t] for t in options])[0]
        domains[cell] = {pick}
        if not propagate(cell):
            return None
        undecided = {c for c, d in domains.items() if len(d) > 1}

    return {c: next(iter(d)) for c, d in domains.items()}


_ADJ = ("Toppled", "Overgrown", "Nameless", "Drowned", "Elder", "Moss-eaten")
_NOUN = ("Colonnade", "Foundations", "Sanctum", "Terrace", "Amphitheatre", "Folly")


def _name(rng) -> str:
    return f"The {rng.choice(_ADJ)} {rng.choice(_NOUN)}"
