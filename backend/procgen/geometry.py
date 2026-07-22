"""Shared shape & placement helpers every generator leans on.

Whatever technique produced the terrain — automata, BSP, noise, WFC — the last
mile is always the same three jobs, so they live here once:

  1. make the map ONE connected piece (`keep_largest_region`),
  2. give it entries the player can arrive through (`punch_border_entries`),
  3. decorate ONLY tiles proven reachable from an entry (`flood_floor`,
     `pick_spawns`, `take_free`).

That shared last mile is why every preset passes the same validator: the
techniques differ wildly in how they invent shape, but they all hand their
shape to the same connectivity-and-placement machinery.
"""
import random

from backend.models import ObjectType, TileType
from backend.procgen.base import ENEMY_IDS
from backend.room_validation import SPAWN_NEAR_ENTRY_RADIUS

FLOOR = TileType.FLOOR.value
WALL = TileType.WALL.value
DOOR = TileType.DOOR.value
PORTAL = TileType.PORTAL.value


# --- connectivity -------------------------------------------------------------


def flood_floor(grid, w, h, start) -> list[tuple[int, int]]:
    """All FLOOR tiles 4-connected to `start`. Mirrors the validator's own
    flood, so anything placed on these tiles is provably reachable."""
    if grid[start[1]][start[0]] != FLOOR:
        return []
    seen = {start}
    stack = [start]
    out = []
    while stack:
        x, y = stack.pop()
        out.append((x, y))
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen
                    and grid[ny][nx] == FLOOR):
                seen.add((nx, ny))
                stack.append((nx, ny))
    return out


def keep_largest_region(grid, w, h) -> list[tuple[int, int]]:
    """Wall-fill every floor region except the biggest; return the kept tiles.
    This is the standard post-pass for techniques (automata, noise, WFC) that
    naturally produce disconnected pockets — one region in, one region out."""
    unvisited = {(x, y) for y in range(h) for x in range(w) if grid[y][x] == FLOOR}
    best: list[tuple[int, int]] = []
    while unvisited:
        region = flood_floor(grid, w, h, next(iter(unvisited)))
        unvisited -= set(region)
        if len(region) > len(best):
            best = region
    keep = set(best)
    for y in range(h):
        for x in range(w):
            if grid[y][x] == FLOOR and (x, y) not in keep:
                grid[y][x] = WALL
    return best


# --- entries ------------------------------------------------------------------


def interior_neighbour(door: tuple[int, int], w: int, h: int) -> tuple[int, int]:
    """The tile just inside a border door — where an arriving player steps."""
    x, y = door
    if y == 0:
        return (x, 1)
    if y == h - 1:
        return (x, h - 2)
    if x == 0:
        return (1, y)
    return (w - 2, y)


def punch_border_entries(grid, w, h, count, rng) -> list[tuple[int, int]]:
    """Open `count` doors in the border wall, each adjacent to existing floor,
    spread across different sides where possible. Call this AFTER
    `keep_largest_region` so every door opens onto the same region.

    If no border tile touches floor (a very inland shape), carves an entry
    tunnel instead — so at least one entry always exists."""
    sides: dict[str, list[tuple[int, int]]] = {"N": [], "S": [], "W": [], "E": []}
    for x in range(1, w - 1):
        if grid[1][x] == FLOOR:
            sides["N"].append((x, 0))
        if grid[h - 2][x] == FLOOR:
            sides["S"].append((x, h - 1))
    for y in range(1, h - 1):
        if grid[y][1] == FLOOR:
            sides["W"].append((0, y))
        if grid[y][w - 2] == FLOOR:
            sides["E"].append((w - 1, y))

    doors: list[tuple[int, int]] = []
    for side in rng.sample(["S", "N", "E", "W"], 4) * count:
        if len(doors) >= count:
            break
        if sides[side]:
            x, y = rng.choice(sides[side])
            sides[side] = []                     # one door per side, spread out
            grid[y][x] = DOOR
            doors.append((x, y))
    while len(doors) < count:                    # no border-adjacent floor left:
        doors.append(_carve_entry_tunnel(grid, w, h, rng))
    return doors


def _carve_entry_tunnel(grid, w, h, rng) -> tuple[int, int]:
    """Dig straight in from the border until hitting floor, leaving a door at
    the border. The guaranteed-entry fallback. Digs from a lane that actually
    contains floor, so the tunnel always joins the map instead of dead-ending."""
    side = rng.choice(["S", "N", "E", "W"])
    if side in ("N", "S"):
        lanes = [x for x in range(1, w - 1)
                 if any(grid[y][x] == FLOOR for y in range(1, h - 1))] or [w // 2]
        x, y = rng.choice(lanes), (0 if side == "N" else h - 1)
        dx, dy = 0, (1 if side == "N" else -1)
    else:
        lanes = [y for y in range(1, h - 1)
                 if any(grid[y][x] == FLOOR for x in range(1, w - 1))] or [h // 2]
        x, y = (0 if side == "W" else w - 1), rng.choice(lanes)
        dx, dy = (1 if side == "W" else -1), 0
    door = (x, y)
    cx, cy = x + dx, y + dy
    while 0 < cx < w - 1 and 0 < cy < h - 1 and grid[cy][cx] != FLOOR:
        grid[cy][cx] = FLOOR
        cx, cy = cx + dx, cy + dy
    grid[door[1]][door[0]] = DOOR
    return door


# --- population ---------------------------------------------------------------


def pick_spawns(entries, reachable, capacity, occupied) -> list[tuple[int, int]]:
    """Up to `capacity` spawn tiles clustered at the first entry. Tiles inside
    the validator's near-entry radius of ANY entry rank first, then by distance
    to entry 0 — so a valid cluster is found whenever one exists."""
    e0 = entries[0]

    def near_any_entry(t):
        return any(max(abs(t[0] - ex), abs(t[1] - ey)) <= SPAWN_NEAR_ENTRY_RADIUS
                   for ex, ey in entries)

    ranked = sorted(reachable, key=lambda t: (
        not near_any_entry(t), max(abs(t[0] - e0[0]), abs(t[1] - e0[1]))))
    picked = ranked[:capacity]
    occupied.update(picked)
    return picked


def take_free(reachable, occupied, n, rng) -> list[tuple[int, int]]:
    """Up to `n` free reachable tiles, chosen at random and marked occupied.
    Yields fewer if the room is too full — a count is a request, not a
    guarantee (small rooms simply hold less)."""
    free = [t for t in reachable if t not in occupied]
    rng.shuffle(free)
    chosen = free[:n]
    occupied.update(chosen)
    return chosen


def take_blocking_free(
        reachable, occupied, blocked, protected, n, rng) -> list[tuple[int, int]]:
    """Choose one-tile objects without cutting the floor graph in two.

    Actors merely reserve a starting tile; objects remain static blockers. A
    candidate is accepted only when all remaining floor tiles stay connected
    after it and previously chosen objects are removed.
    """
    if n <= 0:
        return []

    floor = set(reachable)
    free = [tile for tile in reachable if tile not in occupied and tile not in protected]
    rng.shuffle(free)
    chosen = []

    for candidate in free:
        remaining = floor - blocked - {candidate}
        if not _tiles_connected(remaining):
            continue

        chosen.append(candidate)
        occupied.add(candidate)
        blocked.add(candidate)
        if len(chosen) == n:
            break
    return chosen


def blocking_candidates(reachable, occupied=(), blocked=(), protected=()):
    """Free cells that one more static one-tile object may safely occupy."""
    floor = set(reachable)
    blocked = set(blocked)
    unavailable = set(occupied) | set(protected)
    return [
        tile for tile in reachable
        if tile not in unavailable and _tiles_connected(floor - blocked - {tile})
    ]


def _tiles_connected(tiles) -> bool:
    if not tiles:
        return True
    start = next(iter(tiles))
    seen = {start}
    stack = [start]
    while stack:
        x, y = stack.pop()
        for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if neighbour in tiles and neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return len(seen) == len(tiles)


def populate_contents(reachable, occupied, params, rng, passages=()) -> tuple[list, list]:
    """The standard decoration pass: enemies, chests and fire barrels onto free
    reachable tiles, driven by the preset's counts. Returns (enemy_spawns,
    objects) in the room-dict shape the validator expects. Chests are bare
    positions — contents are rolled when a player opens one (docs/LOOT.md),
    so generation decides WHERE loot can be found, never WHAT it is."""
    enemy_spawns = [
        {"enemy_id": rng.choice(ENEMY_IDS), "x": x, "y": y}
        for (x, y) in take_free(reachable, occupied, params.get("enemies", 0), rng)
    ]
    objects = []
    floor = set(reachable)
    # A border door has one interior floor neighbour; an interior portal may
    # have several. Static props never take those approach cells.
    protected = {
        neighbour
        for x, y in passages
        for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
        if neighbour in floor
    }
    blocked: set[tuple[int, int]] = set()
    for (x, y) in take_blocking_free(
            reachable, occupied, blocked, protected, params.get("chests", 0), rng):
        objects.append({"type": ObjectType.CHEST.value, "x": x, "y": y})
    for (x, y) in take_blocking_free(
            reachable, occupied, blocked, protected, params.get("barrels", 0), rng):
        objects.append({"type": ObjectType.FIRE_BARREL.value, "x": x, "y": y,
                        "hp": rng.randint(2, 4)})
    return enemy_spawns, objects
