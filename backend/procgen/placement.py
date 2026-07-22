"""The shape/population seam: room contents as a separate, swappable stage.

Layer 0 generators place their own contents (geometry.populate_contents) —
that remains the pure-code mode. AI placement needs the same geometry WITHOUT
contents, a menu of tiles that may legally hold them, and a way to merge a
proposal back in. All three helpers work on the ROOM DICT — the one contract —
never on generator internals, so they apply equally to generated and
hand-authored rooms.

Deliberately absent for now: proposal validation. `apply_placement` is a raw
merge; the caller runs the merged room through the real gate (base.validate)
and shows the verdict. The repair loop (feed the gate's error back to the LLM)
comes once this structure has proven itself — see docs/PROCGEN.md.
"""
from backend.models import TileType
from backend.object_defs import get_object_definition, occupied_cells
from backend.procgen.geometry import blocking_candidates, flood_floor

# Params that describe contents rather than shape. AI placement zeroes them —
# the LLM decides contents — and the harness hides their knobs in that mode.
CONTENT_PARAMS = ("enemies", "chests", "barrels")


def shape_params(params: dict | None) -> dict:
    """A copy of `params` with every content count zeroed, so a generator
    emits bare geometry: terrain, entries, and spawns only. Spawns stay
    code-placed — where players arrive is an engine invariant, not flavor."""
    out = dict(params or {})
    for name in CONTENT_PARAMS:
        out[name] = 0
    return out


def candidate_tiles(room: dict) -> list[list[int]]:
    """Floor tiles reachable from spawn 0 that hold nothing yet — the closed
    menu of coordinates a placement proposal may draw from. Because the same
    menu serves actors and static objects, it omits cells where a blocker would
    cut the floor graph or close a passage approach."""
    spawns = [tuple(p) for p in room.get("spawn_points", [])]
    if not spawns:
        return []
    taken = set(spawns)
    taken.update((e["x"], e["y"]) for e in room.get("enemy_spawns", []))
    blocked = set()
    for obj in room.get("objects", []):
        definition = get_object_definition(obj.get("type"))
        cells = (occupied_cells(definition, obj["x"], obj["y"])
                 if definition else ((obj["x"], obj["y"]),))
        taken.update(cells)
        if definition is None or definition.blocks_movement:
            blocked.update(cells)
    reachable = flood_floor(room["terrain"], room["width"], room["height"], spawns[0])
    passages = {
        (x, y)
        for y, row in enumerate(room["terrain"])
        for x, char in enumerate(row)
        if TileType(char) in (TileType.DOOR, TileType.PORTAL)
    }
    floor = set(reachable)
    protected = {
        neighbour
        for x, y in passages
        for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
        if neighbour in floor
    }
    return [
        [x, y]
        for x, y in blocking_candidates(reachable, taken, blocked, protected)
    ]


def apply_placement(room: dict, proposal: dict) -> dict:
    """Merge a placement proposal into a copy of `room`. Raw, trusting merge —
    junk shapes are skipped, but coordinates and ids go in as proposed and the
    real validator judges the result. Accepts the proposal-schema fields:
    `name` (optional rename), `enemy_spawns`, `objects`."""
    out = {
        **room,
        "enemy_spawns": list(room.get("enemy_spawns", [])),
        "objects": list(room.get("objects", [])),
    }
    name = proposal.get("name")
    if isinstance(name, str) and name.strip():
        out["name"] = name.strip()[:80]
    for e in proposal.get("enemy_spawns") or []:
        if isinstance(e, dict):
            out["enemy_spawns"].append(e)
    for o in proposal.get("objects") or []:
        if isinstance(o, dict):
            # Models that learned the pre-loot-system schema still emit a
            # chest "loot" list; contents are rolled at open time now, so the
            # key is dropped as harmless noise rather than failing the room.
            o = {k: v for k, v in o.items() if k != "loot"}
            out["objects"].append(o)
    return out
