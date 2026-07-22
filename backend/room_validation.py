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
  - objects:   known trusted definition + the minimal metadata that type needs
  - entry:     every spawn sits near a door/portal — players arrive at an entry
  - reachable: entries and actors remain reachable around blocking objects
"""
from collections import deque

from backend.models import TileType
from backend.object_defs import get_object_definition, known_object_types, occupied_cells

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

    def place(x: int, y: int, what: str, *, floor_only: bool = False) -> None:
        if not in_bounds(x, y):
            raise ValueError(f"{what} at ({x}, {y}) is out of bounds ({width}x{height})")
        if not tile(x, y).passable:
            raise ValueError(f"{what} at ({x}, {y}) sits on a {tile(x, y).name} tile (not walkable)")
        if floor_only and tile(x, y) is not TileType.FLOOR:
            raise ValueError(f"{what} at ({x}, {y}) must sit on a FLOOR tile, not {tile(x, y).name}")
        if (x, y) in occupied:
            raise ValueError(f"{what} overlaps {occupied[(x, y)]} at ({x}, {y})")
        occupied[(x, y)] = what

    spawns = data["spawn_points"]
    if not isinstance(spawns, list) or not spawns:
        raise ValueError("level needs at least one spawn point (room capacity = number of spawns)")
    spawn_xy = [_as_xy(p, f"spawn point {i}") for i, p in enumerate(spawns)]
    for i, (sx, sy) in enumerate(spawn_xy):
        place(sx, sy, f"spawn point {i}")

    enemy_xy = []
    for i, e in enumerate(data.get("enemy_spawns", [])):
        if "enemy_id" not in e or not isinstance(e["enemy_id"], int):
            raise ValueError(f"enemy spawn {i} needs an int 'enemy_id' (rooms reference enemies by DB id)")
        if "x" not in e or "y" not in e:
            raise ValueError(f"enemy spawn {i} missing x/y")
        place(e["x"], e["y"], f"enemy_id {e['enemy_id']}")
        enemy_xy.append((e["x"], e["y"]))

    blocked_object_cells: set[tuple[int, int]] = set()
    reachable_object_cells: list[tuple[int, int]] = []
    placement_ids: set[str] = set()
    for i, o in enumerate(data.get("objects", [])):
        if not isinstance(o, dict) or "type" not in o:
            raise ValueError(f"object {i} missing 'type'")
        definition = get_object_definition(o["type"]) if isinstance(o["type"], str) else None
        if definition is None:
            raise ValueError(f"unknown object type '{o['type']}' — valid: {list(known_object_types())}")
        if "x" not in o or "y" not in o:
            raise ValueError(f"object {i} ('{o['type']}') missing x/y")
        placement_id = o.get("id")
        if placement_id is not None:
            if not isinstance(placement_id, str) or not placement_id:
                raise ValueError(f"object {i} id must be a non-empty string")
            if placement_id in placement_ids:
                raise ValueError(f"duplicate object placement id '{placement_id}'")
            placement_ids.add(placement_id)
        cells = occupied_cells(definition, o["x"], o["y"])
        for x, y in cells:
            place(x, y, f"object '{o['type']}'", floor_only=True)
        if definition.blocks_movement:
            blocked_object_cells.update(cells)
        else:
            reachable_object_cells.extend(cells)
        # Minimal per-type metadata (deep behavior validation lands with each
        # object's own issue — here we only guarantee the shape is sane).
        # Chests need nothing beyond a position: contents are rolled at open
        # (loot.spawn_loot), never designed into the room (docs/LOOT.md).
        if definition.id == "chest" and "loot" in o:
            raise ValueError(f"chest at ({o['x']}, {o['y']}) must not carry a 'loot' "
                             "list — loot is rolled when a player opens it")
        if definition.id == "fire_barrel" and not isinstance(o.get("hp"), int):
            raise ValueError(f"fire_barrel at ({o['x']}, {o['y']}) needs an int 'hp'")

    # Entries: door/portal tiles. Spawns must cluster around one of them.
    doors = [(x, y) for y in range(height) for x in range(width)
             if tile(x, y) in (TileType.DOOR, TileType.PORTAL)]
    for sx, sy in spawn_xy:
        if not any(max(abs(sx - dx), abs(sy - dy)) <= SPAWN_NEAR_ENTRY_RADIUS for dx, dy in doors):
            raise ValueError(f"spawn ({sx}, {sy}) is not within {SPAWN_NEAR_ENTRY_RADIUS} tiles of an entry (door/portal)")

    _check_reachable(
        terrain, width, height, spawn_xy,
        [*spawn_xy, *enemy_xy, *reachable_object_cells, *doors],
        blocked_object_cells,
    )


def _check_reachable(terrain, width, height, spawn_xy, must_reach, blocked_cells=()) -> None:
    """Flood-fill walkable tiles from the first spawn; everything that must be
    playable has to be in the reached set."""
    blocked = set(blocked_cells)

    def passable(x, y):
        return TileType(terrain[y][x]).passable and (x, y) not in blocked

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
            raise ValueError(f"({x}, {y}) is walled off or blocked — unreachable from spawn {start}")


def validate_enemy_refs(data: dict, known_ids) -> None:
    """The enemy_id in a room's JSON is a *soft* reference (not a DB foreign
    key, because it lives in JSON) — so we check it points at a real enemy_def."""
    for e in data.get("enemy_spawns", []):
        if e["enemy_id"] not in known_ids:
            raise ValueError(f"enemy_id {e['enemy_id']} is not a known enemy definition")


def validate_npc_placement(room: dict, x: int, y: int) -> None:
    """An NPC must stand on a plain floor tile: walls are unwalkable, and
    NPCs occupy their tile (NPCS.md Decision 2) so parking one on a door or
    portal would block the room's entry. Overlap with spawns/enemies/objects
    is checked at seed time only — at runtime individuals move freely."""
    if not (0 <= y < room["height"] and 0 <= x < room["width"]):
        raise ValueError(f"NPC at ({x}, {y}) is out of bounds for room '{room['name']}'")
    tile = TileType(room["terrain"][y][x])
    if tile is not TileType.FLOOR:
        raise ValueError(f"NPC at ({x}, {y}) in '{room['name']}' must be on floor, not {tile.name}")
    for i, p in enumerate(room.get("spawn_points", [])):
        if (p[0], p[1]) == (x, y):
            raise ValueError(f"NPC at ({x}, {y}) overlaps spawn point {i}")
    for e in room.get("enemy_spawns", []):
        if (e["x"], e["y"]) == (x, y):
            raise ValueError(f"NPC at ({x}, {y}) overlaps enemy_id {e['enemy_id']}")
    for o in room.get("objects", []):
        definition = get_object_definition(o["type"])
        if definition is None:
            raise ValueError(f"unknown object type '{o['type']}'")
        if (x, y) in occupied_cells(definition, o["x"], o["y"]):
            raise ValueError(f"NPC at ({x}, {y}) overlaps object '{o['type']}'")


def validate_connection(from_room: dict, conn: dict) -> None:
    """A connection must originate on an actual door/portal tile of from_room."""
    x, y = conn["from_x"], conn["from_y"]
    if not (0 <= y < from_room["height"] and 0 <= x < from_room["width"]):
        raise ValueError(f"connection origin ({x}, {y}) is out of bounds for room '{from_room['name']}'")
    if TileType(from_room["terrain"][y][x]) not in (TileType.DOOR, TileType.PORTAL):
        raise ValueError(f"connection origin ({x}, {y}) in '{from_room['name']}' is not a door/portal tile")
