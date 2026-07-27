"""Story-shaped wilderness rooms for the long frontier between regions.

The existing open-land generator is terrain-first. This preset is
composition-first: each archetype creates a recognizable travel problem—a
ravine, a flooded road, an old battlefield, a rot-veined wood—then the shared
connectivity pass guarantees it remains playable.
"""

from __future__ import annotations

import math
import random

from backend.procgen.base import Param
from backend.procgen.geometry import (
    DOOR,
    FLOOR,
    PORTAL,
    WALL,
    flood_floor,
    interior_neighbour,
    keep_largest_region,
    pick_spawns,
    populate_contents,
    punch_border_entries,
)

ARCHETYPES = (
    "pilgrim_road",
    "braided_river",
    "ravine_crossing",
    "old_battlefield",
    "rotwood",
    "black_marsh",
    "grave_moor",
    "caravan_remains",
)

PARAMS = (
    Param("width", "int", "Width", 30, min=20, max=42),
    Param("height", "int", "Height", 20, min=14, max=30),
    Param(
        "archetype",
        "choice",
        "Travel story",
        "pilgrim_road",
        options=ARCHETYPES,
        help="The landscape's gameplay problem and visual grammar.",
    ),
    Param("entries", "int", "Frontier exits", 3, min=1, max=4),
    Param("secrets", "int", "Hidden passages", 1, min=0, max=3),
    Param("capacity", "int", "Player spawns", 4, min=1, max=6),
    Param("enemies", "int", "Enemies", 6, min=0, max=14),
    Param("chests", "int", "Chests", 1, min=0, max=4),
    Param("barrels", "int", "Fire barrels", 0, min=0, max=3),
)


def generate(params: dict, rng: random.Random) -> dict:
    w, h = params["width"], params["height"]
    grid = [
        [WALL if x in (0, w - 1) or y in (0, h - 1) else FLOOR
         for x in range(w)]
        for y in range(h)
    ]

    archetype = params["archetype"]
    {
        "pilgrim_road": _pilgrim_road,
        "braided_river": _braided_river,
        "ravine_crossing": _ravine_crossing,
        "old_battlefield": _old_battlefield,
        "rotwood": _rotwood,
        "black_marsh": _black_marsh,
        "grave_moor": _grave_moor,
        "caravan_remains": _caravan_remains,
    }[archetype](grid, w, h, rng)

    kept = keep_largest_region(grid, w, h)
    if len(kept) < (w - 2) * (h - 2) // 4:
        # A dramatic pattern may close too aggressively at an extreme size.
        # Preserve its silhouette but open a broad cross as a safe fallback.
        _carve_line(grid, (1, h // 2), (w - 2, h // 2), width=2)
        _carve_line(grid, (w // 2, 1), (w // 2, h - 2), width=2)
        keep_largest_region(grid, w, h)

    entries = punch_border_entries(
        grid, w, h, params["entries"], rng
    )
    entry = interior_neighbour(entries[0], w, h)
    # A visible, imperfect road links all exits. Connectivity already holds,
    # but this makes navigation legible and produces story-like crossings.
    for other in entries[1:]:
        _drunken_path(
            grid,
            entry,
            interior_neighbour(other, w, h),
            rng,
            width=1 if archetype in {"rotwood", "grave_moor"} else 2,
        )

    reachable = flood_floor(grid, w, h, entry)
    secret_tiles = _place_secret_portals(
        grid,
        reachable,
        entries,
        params["secrets"],
        rng,
    )
    usable = [tile for tile in reachable if tile not in set(secret_tiles)]

    occupied: set[tuple[int, int]] = set()
    spawns = pick_spawns(entries, usable, params["capacity"], occupied)
    enemy_spawns, objects = populate_contents(
        usable,
        occupied,
        params,
        rng,
        [*entries, *secret_tiles],
    )
    # A shape-only generation is the blank canvas used by the optional AI
    # population seam. Preserve that contract; normal frontier rooms still
    # receive one archetype-specific landmark alongside their contents.
    if any(params[name] > 0 for name in ("enemies", "chests", "barrels")):
        _place_landmark(
            objects,
            usable,
            occupied,
            entries,
            archetype,
            rng,
        )

    return {
        "name": _name(archetype, rng),
        "width": w,
        "height": h,
        "terrain": ["".join(row) for row in grid],
        "spawn_points": [[x, y] for x, y in spawns],
        "enemy_spawns": enemy_spawns,
        "objects": objects,
    }


def _pilgrim_road(grid, w, h, rng):
    """Hedged lanes widen around abandoned gathering places."""
    mid = h // 2 + rng.randint(-2, 2)
    for x in range(2, w - 2):
        bend = round(math.sin(x / 4) * 2)
        for y in range(1, h - 1):
            if abs(y - (mid + bend)) > rng.choice((3, 4, 5)):
                if rng.random() < 0.5:
                    grid[y][x] = WALL
    for _ in range(max(2, w // 12)):
        cx, cy = rng.randint(4, w - 5), rng.randint(3, h - 4)
        _clear_disc(grid, cx, cy, rng.randint(2, 3))


def _braided_river(grid, w, h, rng):
    """Two water channels with several unreliable island crossings."""
    vertical = rng.random() < 0.5
    length = h if vertical else w
    for band in (-3, 3):
        drift = rng.randint(-2, 2)
        for step in range(1, length - 1):
            wave = round(math.sin((step + drift) / 3) * 2)
            center = (w // 2 if vertical else h // 2) + band + wave
            for offset in (-1, 0, 1):
                x, y = (center + offset, step) if vertical else (step, center + offset)
                if 0 < x < w - 1 and 0 < y < h - 1:
                    grid[y][x] = WALL
    # Ford/bridge cuts prevent the channels being mere solid dividers.
    for _ in range(4):
        if vertical:
            y = rng.randint(2, h - 3)
            _carve_line(grid, (1, y), (w - 2, y), width=1)
        else:
            x = rng.randint(2, w - 3)
            _carve_line(grid, (x, 1), (x, h - 2), width=1)


def _ravine_crossing(grid, w, h, rng):
    """A diagonal canyon crossed by narrow causeways."""
    slope = (h - 6) / max(1, w - 6)
    for x in range(2, w - 2):
        center = 3 + slope * (x - 2) + math.sin(x / 3) * 2
        for y in range(1, h - 1):
            if abs(y - center) <= 2 + (1 if rng.random() < 0.15 else 0):
                grid[y][x] = WALL
    for x in sorted(rng.sample(range(4, w - 4), k=min(3, max(1, w - 8)))):
        center = round(3 + slope * (x - 2) + math.sin(x / 3) * 2)
        _carve_line(
            grid,
            (x - 1, max(1, center - 4)),
            (x + 1, min(h - 2, center + 4)),
            width=1,
        )


def _old_battlefield(grid, w, h, rng):
    """Blast craters, broken formations, and long traversable trenches."""
    for _ in range(max(5, (w * h) // 90)):
        cx, cy = rng.randint(3, w - 4), rng.randint(3, h - 4)
        radius = rng.randint(1, 3)
        for y in range(cy - radius, cy + radius + 1):
            for x in range(cx - radius, cx + radius + 1):
                dist = abs(x - cx) + abs(y - cy)
                if radius <= dist <= radius + 1 and rng.random() < 0.8:
                    grid[y][x] = WALL
    for _ in range(3):
        start = (rng.randint(2, w - 3), rng.randint(2, h - 3))
        end = (rng.randint(2, w - 3), rng.randint(2, h - 3))
        _drunken_path(grid, start, end, rng, width=1)


def _rotwood(grid, w, h, rng):
    """Branching black growth leaves chambers between its veins."""
    for _ in range(max(3, w // 8)):
        x, y = rng.randint(3, w - 4), rng.randint(3, h - 4)
        length = rng.randint(8, 18)
        dx, dy = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
        for _step in range(length):
            if not (1 < x < w - 2 and 1 < y < h - 2):
                break
            grid[y][x] = WALL
            if rng.random() < 0.35:
                grid[min(h - 2, max(1, y + rng.choice((-1, 1))))][
                    min(w - 2, max(1, x + rng.choice((-1, 1))))
                ] = WALL
            if rng.random() < 0.3:
                dx, dy = rng.choice(((dx, dy), (-dy, dx), (dy, -dx)))
            x, y = x + dx, y + dy


def _black_marsh(grid, w, h, rng):
    """Noise-like pools joined by reed-thin strips of dry ground."""
    for y in range(2, h - 2):
        for x in range(2, w - 2):
            wave = math.sin(x / 2.7) + math.cos(y / 3.2)
            if wave + rng.uniform(-1.4, 1.4) > 1.0:
                grid[y][x] = WALL
    _carve_line(grid, (1, h // 2), (w - 2, h // 2), width=1)
    _carve_line(grid, (w // 2, 1), (w // 2, h - 2), width=1)


def _grave_moor(grid, w, h, rng):
    """Ordered grave rows slowly lose alignment toward the frontier."""
    for y in range(3, h - 2, 3):
        offset = rng.randint(-1, 1)
        for x in range(3 + offset, w - 2, 3):
            if rng.random() < 0.75:
                grid[y][x] = WALL
                if rng.random() < 0.25 and x + 1 < w - 1:
                    grid[y][x + 1] = WALL
    _clear_disc(grid, w // 2, h // 2, 3)


def _caravan_remains(grid, w, h, rng):
    """Concentric wreck-lines form a defensible but compromised camp."""
    cx, cy = w // 2, h // 2
    for radius in range(3, min(w, h) // 2, 4):
        for angle_step in range(0, 360, 12):
            if rng.random() < 0.35:
                continue
            angle = math.radians(angle_step)
            x = round(cx + math.cos(angle) * radius * 1.5)
            y = round(cy + math.sin(angle) * radius)
            if 1 < x < w - 2 and 1 < y < h - 2:
                grid[y][x] = WALL
        # Each ring has at least two breaches.
        for _ in range(2):
            angle = rng.random() * math.tau
            _clear_disc(
                grid,
                round(cx + math.cos(angle) * radius * 1.5),
                round(cy + math.sin(angle) * radius),
                1,
            )


def _place_secret_portals(grid, reachable, entries, count, rng):
    if count <= 0:
        return []
    protected = {
        tile
        for tile in reachable
        if any(abs(tile[0] - ex) + abs(tile[1] - ey) < 5 for ex, ey in entries)
    }
    candidates = [tile for tile in reachable if tile not in protected]
    rng.shuffle(candidates)
    chosen: list[tuple[int, int]] = []
    for x, y in candidates:
        if all(abs(x - px) + abs(y - py) >= 6 for px, py in chosen):
            grid[y][x] = PORTAL
            chosen.append((x, y))
            if len(chosen) == count:
                break
    return chosen


def _drunken_path(grid, start, end, rng, width=1):
    x, y = start
    tx, ty = end
    for _ in range((len(grid) + len(grid[0])) * 4):
        _clear_disc(grid, x, y, width - 1)
        grid[y][x] = FLOOR
        if (x, y) == (tx, ty):
            return
        if rng.random() < 0.78:
            if abs(tx - x) >= abs(ty - y):
                x += 1 if tx > x else -1 if tx < x else 0
            else:
                y += 1 if ty > y else -1 if ty < y else 0
        else:
            dx, dy = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
            nx, ny = x + dx, y + dy
            if 0 < nx < len(grid[0]) - 1 and 0 < ny < len(grid) - 1:
                x, y = nx, ny
    _carve_line(grid, (x, y), (tx, ty), width=width)


def _carve_line(grid, start, end, width=1):
    x, y = start
    tx, ty = end
    while (x, y) != (tx, ty):
        _clear_disc(grid, x, y, width - 1)
        grid[y][x] = FLOOR
        if x != tx:
            x += 1 if tx > x else -1
        elif y != ty:
            y += 1 if ty > y else -1
    _clear_disc(grid, tx, ty, width - 1)
    grid[ty][tx] = FLOOR


def _clear_disc(grid, cx, cy, radius):
    h, w = len(grid), len(grid[0])
    for y in range(max(1, cy - radius), min(h - 1, cy + radius + 1)):
        for x in range(max(1, cx - radius), min(w - 1, cx + radius + 1)):
            if abs(x - cx) + abs(y - cy) <= max(1, radius):
                grid[y][x] = FLOOR


_NAMES = {
    "pilgrim_road": (
        "The Candle-Mile",
        "Saintless Road",
        "The Long Petition",
        "Pilgrims' Turn",
    ),
    "braided_river": (
        "Nine-Ford Water",
        "The Divided Wash",
        "Eelbraid Crossing",
        "Morrow's Channels",
    ),
    "ravine_crossing": (
        "The Split King's Step",
        "Rookfall Ravine",
        "The Narrow Mercy",
        "Wind-Cut Crossing",
    ),
    "old_battlefield": (
        "The Unfinished Charge",
        "Bannerless Acres",
        "The Quiet Rout",
        "Widows' Measure",
    ),
    "rotwood": (
        "Sweet-Iron Wood",
        "The Black Veins",
        "Scentless Copse",
        "The Listening Timber",
    ),
    "black_marsh": (
        "Candle-Sink Fen",
        "The Unreflected Mire",
        "Blackreed Waste",
        "Pilgrim's Drowning",
    ),
    "grave_moor": (
        "The Crooked Census",
        "Namesward Moor",
        "The Last Parish",
        "Hundred-Stone Heath",
    ),
    "caravan_remains": (
        "The Ringed Wreck",
        "Axle-Crown Camp",
        "The Last Encampment",
        "Canvas-Bone Hollow",
    ),
}


def _name(archetype, rng):
    return rng.choice(_NAMES[archetype])


_LANDMARKS = {
    "pilgrim_road": "frontier_candle_stone",
    "braided_river": "frontier_drowned_bell",
    "ravine_crossing": "frontier_rope_throne",
    "old_battlefield": "frontier_soldier_cairn",
    "rotwood": "frontier_root_mirror",
    "black_marsh": "frontier_reed_door",
    "grave_moor": "frontier_blank_grave",
    "caravan_remains": "frontier_last_manifest",
}


def _place_landmark(objects, usable, occupied, entries, archetype, rng):
    candidates = [
        tile
        for tile in usable
        if tile not in occupied
        and all(abs(tile[0] - x) + abs(tile[1] - y) >= 5 for x, y in entries)
    ]
    if not candidates:
        return
    # Prefer a memorable side-space over the center of the mandatory road.
    rng.shuffle(candidates)
    x, y = max(
        candidates[: min(30, len(candidates))],
        key=lambda tile: min(
            abs(tile[0] - ex) + abs(tile[1] - ey)
            for ex, ey in entries
        ),
    )
    objects.append({
        "id": f"generated_landmark_{archetype}",
        "type": _LANDMARKS[archetype],
        "x": x,
        "y": y,
    })
    occupied.add((x, y))
