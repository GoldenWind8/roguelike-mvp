"""Validate-then-store for level data.

A level is a variable-shape JSON blob, so the database can't enforce its rules
— we do, here, before any insert. Every failure raises ValueError with a
specific message: that message is also exactly what you'd feed back to an LLM
as a repair prompt when it generates a malformed room.

What we check:
  - structure: required fields, correct types
  - terrain:   right number of rows, right width, only known tile chars
  - placement: spawns/enemies/objects are in bounds, on walkable tiles, and
               don't overlap each other
  - enemies:   reference an enemy by id (stats live in the enemy_defs table)
  - objects:   known ObjectType + the minimal metadata that type needs
  - entry:     every spawn sits near a door/portal — players arrive at an entry
  - reachable: every spawn/enemy/object/door is reachable on foot from spawn 0
"""
from collections import deque

from backend.models import ObjectType, TileType

_REQUIRED = ("name", "width", "height", "terrain", "spawn_points")

# How close (Chebyshev / king-move distance) a spawn must be to a door/portal.
# Encodes "players spawn around the room's entry".
SPAWN_NEAR_ENTRY_RADIUS = 2


def _as_xy(value, what: str) -> tuple[int, int]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(c, int) for c in value)):
        raise ValueError(f"{what} must be [x, y] of two ints, got {value!r}")
    return value[0], value[1]


def validate_room(data: dict) -> None:
    """Raise ValueError if `data` is not a storable level. Returns None on success."""
    for field in _REQUIRED:
        if field not in data:
            raise ValueError(f"level missing required field '{field}'")

    width, height = data["width"], data["height"]
    if not isinstance(width, int) or width <= 0:
        raise ValueError(f"width must be a positive int, got {width!r}")
    if not isinstance(height, int) or height <= 0:
        raise ValueError(f"height must be a positive int, got {height!r}")

    terrain = data["terrain"]
    if not isinstance(terrain, list) or len(terrain) != height:
        raise ValueError(f"terrain must be {height} rows, got {len(terrain) if isinstance(terrain, list) else type(terrain).__name__}")
    for y, row in enumerate(terrain):
        if not isinstance(row, str) or len(row) != width:
            raise ValueError(f"terrain row {y} must be {width} chars wide, got {len(row) if isinstance(row, str) else row!r}")
        for x, ch in enumerate(row):
            try:
                TileType(ch)
            except ValueError:
                raise ValueError(f"unknown tile '{ch}' at ({x}, {y}) — valid: {[t.value for t in TileType]}")

    def tile(x: int, y: int) -> TileType:
        return TileType(terrain[y][x])

    def in_bounds(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height

    # Track every occupied cell so two things can't share a tile.
    occupied: dict[tuple[int, int], str] = {}

    def place(x: int, y: int, what: str) -> None:
        if not in_bounds(x, y):
            raise ValueError(f"{what} at ({x}, {y}) is out of bounds ({width}x{height})")
        if not tile(x, y).passable:
            raise ValueError(f"{what} at ({x}, {y}) sits on a {tile(x, y).name} tile (not walkable)")
        if (x, y) in occupied:
            raise ValueError(f"{what} overlaps {occupied[(x, y)]} at ({x}, {y})")
        occupied[(x, y)] = what

    spawns = data["spawn_points"]
    if not isinstance(spawns, list) or not spawns:
        raise ValueError("level needs at least one spawn point (room capacity = number of spawns)")
    spawn_xy = [_as_xy(p, f"spawn point {i}") for i, p in enumerate(spawns)]
    for i, (sx, sy) in enumerate(spawn_xy):
        place(sx, sy, f"spawn point {i}")

    for i, e in enumerate(data.get("enemy_spawns", [])):
        if "enemy_id" not in e or not isinstance(e["enemy_id"], int):
            raise ValueError(f"enemy spawn {i} needs an int 'enemy_id' (rooms reference enemies by DB id)")
        if "x" not in e or "y" not in e:
            raise ValueError(f"enemy spawn {i} missing x/y")
        place(e["x"], e["y"], f"enemy_id {e['enemy_id']}")

    for i, o in enumerate(data.get("objects", [])):
        if "type" not in o:
            raise ValueError(f"object {i} missing 'type'")
        try:
            otype = ObjectType(o["type"])
        except ValueError:
            raise ValueError(f"unknown object type '{o['type']}' — valid: {[t.value for t in ObjectType]}")
        if "x" not in o or "y" not in o:
            raise ValueError(f"object {i} ('{o['type']}') missing x/y")
        place(o["x"], o["y"], f"object '{o['type']}'")
        # Minimal per-type metadata (deep behavior validation lands with each
        # object's own issue — here we only guarantee the shape is sane).
        if otype is ObjectType.CHEST and not isinstance(o.get("loot"), list):
            raise ValueError(f"chest at ({o['x']}, {o['y']}) needs a 'loot' list")
        if otype is ObjectType.FIRE_BARREL and not isinstance(o.get("hp"), int):
            raise ValueError(f"fire_barrel at ({o['x']}, {o['y']}) needs an int 'hp'")

    # Entries: door/portal tiles. Spawns must cluster around one of them.
    doors = [(x, y) for y in range(height) for x in range(width)
             if tile(x, y) in (TileType.DOOR, TileType.PORTAL)]
    for sx, sy in spawn_xy:
        if not any(max(abs(sx - dx), abs(sy - dy)) <= SPAWN_NEAR_ENTRY_RADIUS for dx, dy in doors):
            raise ValueError(f"spawn ({sx}, {sy}) is not within {SPAWN_NEAR_ENTRY_RADIUS} tiles of an entry (door/portal)")

    _check_reachable(terrain, width, height, spawn_xy, list(occupied) + doors)


def _check_reachable(terrain, width, height, spawn_xy, must_reach) -> None:
    """Flood-fill walkable tiles from the first spawn; everything that must be
    playable has to be in the reached set."""
    def passable(x, y):
        return TileType(terrain[y][x]).passable

    start = spawn_xy[0]
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in seen and passable(nx, ny):
                seen.add((nx, ny))
                q.append((nx, ny))

    for x, y in must_reach:
        if (x, y) not in seen:
            raise ValueError(f"({x}, {y}) is walled off — unreachable from spawn {start}")


def validate_enemy_refs(data: dict, known_ids) -> None:
    """The enemy_id in a room's JSON is a *soft* reference (not a DB foreign
    key, because it lives in JSON) — so we check it points at a real enemy_def."""
    for e in data.get("enemy_spawns", []):
        if e["enemy_id"] not in known_ids:
            raise ValueError(f"enemy_id {e['enemy_id']} is not a known enemy definition")


def validate_connection(from_room: dict, conn: dict) -> None:
    """A connection must originate on an actual door/portal tile of from_room."""
    x, y = conn["from_x"], conn["from_y"]
    if not (0 <= y < from_room["height"] and 0 <= x < from_room["width"]):
        raise ValueError(f"connection origin ({x}, {y}) is out of bounds for room '{from_room['name']}'")
    if TileType(from_room["terrain"][y][x]) not in (TileType.DOOR, TileType.PORTAL):
        raise ValueError(f"connection origin ({x}, {y}) in '{from_room['name']}' is not a door/portal tile")
