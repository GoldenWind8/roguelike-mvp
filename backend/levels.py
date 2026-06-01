"""Hand-authored seed data (the format an LLM will emit later).

ENEMY_DEFS is the reusable enemy catalog (stable ids). Rooms reference enemies
by id and store only their placement — stats are loaded from enemy_defs.

DEFAULT_ROOM is a 10x10 pillared hall that drops into the running 10x10
frontend. Players ENTER through the south door and spawn clustered around it;
a far door (north) and the south door both link to SECOND_ROOM, so the world
graph has real edges from day one (walking through them is M3).

Terrain is an ASCII grid (one char per tile, see TileType):
    #  wall      .  floor      +  door      O  portal
"""
from backend.level_validation import (
    validate_connection,
    validate_enemy_refs,
    validate_level,
)
from backend.models import EnemyDef, Room, RoomConnection


# Reusable enemy catalog. Stable, explicit ids so rooms can reference them.
# on_death on the Goblin is an illustrative effect-data hook — its shape is
# validated/executed by the effect system later (#22/M0).
ENEMY_DEFS = [
    {"id": 1, "name": "Goblin",      "hp": 6, "attack_damage": 1, "defense": 1,
     "on_spawn": [], "on_death": [{"effect": "drop_loot", "items": ["coin"]}]},
    {"id": 2, "name": "Skeleton",    "hp": 8, "attack_damage": 2, "defense": 1,
     "on_spawn": [], "on_death": []},
    {"id": 3, "name": "Rat",         "hp": 4, "attack_damage": 3, "defense": 3,
     "on_spawn": [], "on_death": []},
    {"id": 4, "name": "Angry bunny", "hp": 7, "attack_damage": 0, "defense": 2,
     "on_spawn": [], "on_death": []},
]


DEFAULT_ROOM = {
    "name": "The Pillared Hall",
    "width": 10,
    "height": 10,
    "terrain": [
        "####+#####",   # far door (4, 0) -> SECOND_ROOM
        "#........#",
        "#..#..#..#",
        "#........#",
        "#..#..#..#",
        "#........#",
        "#..#..#..#",
        "#........#",
        "#........#",
        "####+#####",   # entry door (4, 9): players arrive and spawn nearby
    ],
    # Capacity = 4. All clustered within 2 tiles of the south entry door.
    "spawn_points": [[3, 8], [4, 8], [5, 8], [4, 7]],
    "enemy_spawns": [
        {"enemy_id": 1, "x": 4, "y": 3},   # Goblin
        {"enemy_id": 4, "x": 5, "y": 3},   # Angry bunny
        {"enemy_id": 3, "x": 2, "y": 5},   # Rat
        {"enemy_id": 2, "x": 7, "y": 5},   # Skeleton
    ],
    "objects": [
        {"type": "chest", "x": 1, "y": 1, "loot": ["health_potion"]},
        {"type": "chest", "x": 8, "y": 1, "loot": ["bomb"]},
        {"type": "fire_barrel", "x": 4, "y": 5, "hp": 3},
    ],
}


SECOND_ROOM = {
    "name": "The Antechamber",
    "width": 7,
    "height": 5,
    "terrain": [
        "#######",
        "#.....#",
        "+.....#",   # door (0, 2) -> back to DEFAULT_ROOM; spawn beside it
        "#.....#",
        "#######",
    ],
    "spawn_points": [[1, 2]],
    "enemy_spawns": [],
    "objects": [],
}


# Edges of the world graph: (from_room_key, to_room_key, from_x, from_y).
# Resolved to real room ids at seed time once the rows have been flushed.
_CONNECTIONS = [
    ("default", "second", 4, 0),   # far door
    ("default", "second", 4, 9),   # entry door
    ("second", "default", 0, 2),   # door back
]


async def seed_default_level(session) -> Room:
    """Validate, insert, and link the enemy catalog + default + second room.
    Returns the default Room (the one the game starts in)."""
    rooms = {"default": DEFAULT_ROOM, "second": SECOND_ROOM}
    known_enemy_ids = {d["id"] for d in ENEMY_DEFS}

    # Validate everything before touching the DB — fail fast, fail loud.
    for data in rooms.values():
        validate_level(data)
        validate_enemy_refs(data, known_enemy_ids)
    for from_key, _to, fx, fy in _CONNECTIONS:
        validate_connection(rooms[from_key], {"from_x": fx, "from_y": fy})

    session.add_all(EnemyDef(**d) for d in ENEMY_DEFS)

    models = {
        key: Room(
            name=d["name"], width=d["width"], height=d["height"],
            terrain=d["terrain"], objects=d["objects"],
            spawn_points=d["spawn_points"], enemy_spawns=d["enemy_spawns"],
        )
        for key, d in rooms.items()
    }
    session.add_all(models.values())
    # flush (not commit) sends the INSERTs so the auto-generated room ids are
    # populated — connections need those ids before we can commit.
    await session.flush()

    for from_key, to_key, fx, fy in _CONNECTIONS:
        session.add(RoomConnection(
            from_room_id=models[from_key].id,
            to_room_id=models[to_key].id,
            from_x=fx, from_y=fy,
        ))
    await session.commit()
    return models["default"]
