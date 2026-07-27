"""Hand-authored seed data (the format an LLM will emit later).

ENEMY_DEFS is the reusable enemy catalog (stable ids). Rooms reference enemies
by id and store only their placement — stats are loaded from enemy_defs.

Oakrun is the production starting slice. The older pillared-hall pair remains
available to focused engine tests, but ``get_or_seed_default_room`` returns the
town so a newly authenticated player wakes at its south road.

Terrain is an ASCII grid (one char per tile, see TileType):
    #  wall      .  floor      +  door      O  portal
"""
from sqlalchemy import delete, select

from backend.content import load_catalog, load_json, load_region
from backend.persona import validate_persona
from backend.room_validation import (
    validate_connection,
    validate_enemy_refs,
    validate_npc_placement,
    validate_room,
)
from backend.models import EnemyDef, NPCRow, PlayerRow, Room, RoomConnection


# Reusable enemy catalog. Stable, explicit ids so rooms can reference them.
# on_spawn/on_death stay empty until a system reads them — "enemies drop loot"
# is the planned first tenant (it will call loot.spawn_loot like every other
# loot source, docs/LOOT.md), and illustrative fake entries would only lie
# about being implemented.
_ENEMY_CONTENT_FIELDS = {
    "id", "name", "hp", "attack_damage", "defense", "on_spawn", "on_death",
}
ENEMY_DEFS = [
    {key: value for key, value in entry.items() if key in _ENEMY_CONTENT_FIELDS}
    for entry in load_json("enemies.json")
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
    # Chests carry NO loot list: contents are rolled by loot.spawn_loot at the
    # moment a player opens one (docs/LOOT.md Decision 1) — a chest is a dumb
    # object whose only design data is where it stands.
    "objects": [
        {"type": "chest", "x": 1, "y": 1},
        {"type": "chest", "x": 8, "y": 1},
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
    # Two spawns so a pair can co-locate here (capacity = spawn count).
    "spawn_points": [[1, 2], [1, 1]],
    "enemy_spawns": [],
    "objects": [],
}


# --- Oakrun: the actual player-facing starting slice -------------------------

_OAKRUN_CONTENT = load_region("world/oakrun/region.json")
OAKRUN_ROOMS = _OAKRUN_CONTENT["rooms"]
OAKRUN_ROOM = OAKRUN_ROOMS[_OAKRUN_CONTENT["start_room"]]
NORTH_ROAD_ROOM = OAKRUN_ROOMS["oakrun_north_road"]
_OAKRUN_ROOM_NAMES = {
    room_id: data["name"] for room_id, data in OAKRUN_ROOMS.items()
}


# The first individual (NPCS.md Decision 9): seeded ONCE as an instance row
# in the Antechamber, then owned by play — reseeding never resurrects or
# moves him, unlike the fungible enemies above. The persona document is the
# format a generator will emit later; it passes the same validation gate.
GORRIK_PERSONA = {
    "id": "gorrik-antechamber",
    "name": "Gorrik",
    "role": "caretaker of the antechamber",
    "persona": (
        "A stooped, gravel-voiced old caretaker who has swept the antechamber "
        "since before anyone can remember. Gruff and economical with words, "
        "secretly glad of any company. Speaks in short, dry sentences and "
        "refers to the dungeon's monsters as 'the tenants'."
    ),
    "drives": [
        "keep the antechamber tidy",
        "learn news from travelers",
        "avoid the tenants' notice",
    ],
    "disposition": "neutral",
    "canned": [
        "Mind the dust. I just swept there.",
        "Hmph. Travelers. Always tracking blood through my hall.",
        "The tenants next door are restless today. Step lightly.",
        "Ask me no favors. I sweep, that is all.",
    ],
    "party_policy": "Will not leave his hall for strangers; decades of habit outweigh any offer.",
}

# A recruitable individual seeded into the COMBAT hall (NPCS.md "Followers" —
# there is no code barrier to an NPC in a combat room; only her room_id differs
# from Gorrik's). Friendly from the start and — unlike Gorrik — her persona
# GRANTS join_party, so warming her isn't needed: parley, recruit, and she
# fights the hall's tenants beside you. The whole M6 loop, live, without NPC
# traversal.
MARA_PERSONA = {
    "id": "mara-pillared-hall",
    "name": "Mara",
    "role": "stranded sellsword",
    "persona": (
        "A restless mercenary who wandered into the hall chasing a contract "
        "that never paid. Blunt, itching for a fight, and quietly grateful for "
        "any excuse to swing her blade at the tenants. Speaks in short, wry lines."
    ),
    "drives": [
        "earn coin with steel",
        "not die bored in this dusty hall",
        "size up whether a traveler can actually fight",
    ],
    "disposition": "friendly",
    "canned": [
        "You look like you could use a blade at your back.",
        "Say the word and I'll carve us a path.",
        "These tenants won't clear themselves.",
    ],
    "party_policy": "Eager to join anyone who means to fight the hall's monsters; a real scrap is payment enough.",
    "grants": ["join_party"],
}


# Production NPC definitions live in content/npcs.json. The old prototype
# personas (Gorrik and Mara) remain local test fixtures.
_AUTHORED_NPCS = load_catalog("npcs.json")


def _persona_from_content(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key not in {"spawn", "stats"}}


BASIL_PERSONA = _persona_from_content(_AUTHORED_NPCS["basil-oakrun"])
ELOWEN_PERSONA = _persona_from_content(_AUTHORED_NPCS["elowen-wayfarers-rest"])
TOM_PERSONA = _persona_from_content(_AUTHORED_NPCS["tom-oakrun-stable"])
HESTER_PERSONA = _persona_from_content(_AUTHORED_NPCS["hester-oakrun-carriage"])
ROWAN_PERSONA = _persona_from_content(_AUTHORED_NPCS["rowan-oakrun-courier"])
MAUD_PERSONA = _persona_from_content(_AUTHORED_NPCS["maud-oakrun-orchard"])
ALYS_PERSONA = _persona_from_content(_AUTHORED_NPCS["alys-oakrun-watch"])

# (room_key, persona, x, y, hp, defense, attack_damage) — placement is
# validated against the room like every other seeded thing. Gorrik is a
# caretaker (modest stats, harmless, no grants); Mara is a combatant (more hp,
# a real attack, and grants join_party) so a recruited follower can actually
# fight beside you.
NPC_SEEDS = [
    ("second", GORRIK_PERSONA, 5, 2, 20, 1, 0),
    ("default", MARA_PERSONA, 2, 8, 40, 1, 8),
]

OAKRUN_NPC_SEEDS = []
for _entry in _AUTHORED_NPCS.values():
    _spawn = _entry["spawn"]
    if _spawn["region"] != "oakrun":
        continue
    _stats = _entry["stats"]
    OAKRUN_NPC_SEEDS.append((
        _spawn["room"], _persona_from_content(_entry), _spawn["x"], _spawn["y"],
        _stats["hp"], _stats["defense"], _stats["attack_damage"],
    ))


# Edges of the world graph: (from_room_key, to_room_key, from_x, from_y).
# Resolved to real room ids at seed time once the rows have been flushed.
_CONNECTIONS = [
    ("default", "second", 4, 0),   # far door
    ("default", "second", 4, 9),   # entry door
    ("second", "default", 0, 2),   # door back
]

_OAKRUN_CONNECTIONS = [
    (connection["from"], connection["to"], connection["x"], connection["y"])
    for connection in _OAKRUN_CONTENT["connections"]
]


def _validate_oakrun_content() -> None:
    """Validate the complete authored region before any database mutation."""
    known_enemy_ids = {definition["id"] for definition in ENEMY_DEFS}
    for data in OAKRUN_ROOMS.values():
        validate_room(data)
        validate_enemy_refs(data, known_enemy_ids)
    connection_tiles: set[tuple[str, int, int]] = set()
    for from_key, _to, fx, fy in _OAKRUN_CONNECTIONS:
        tile = (from_key, fx, fy)
        if tile in connection_tiles:
            raise ValueError(
                f"Oakrun room {from_key!r} has more than one connection at ({fx}, {fy})"
            )
        connection_tiles.add(tile)
        validate_connection(OAKRUN_ROOMS[from_key], {"from_x": fx, "from_y": fy})
    for room_key, persona, x, y, _hp, _defense, _atk in OAKRUN_NPC_SEEDS:
        validate_persona(persona)
        validate_npc_placement(OAKRUN_ROOMS[room_key], x, y)


async def seed_default_rooms(session) -> Room:
    """Validate, insert, and link the enemy catalog + default + second room.
    Returns the default Room (the one the game starts in)."""
    rooms = {"default": DEFAULT_ROOM, "second": SECOND_ROOM}
    known_enemy_ids = {d["id"] for d in ENEMY_DEFS}

    # Validate everything before touching the DB — fail fast, fail loud.
    for data in rooms.values():
        validate_room(data)
        validate_enemy_refs(data, known_enemy_ids)
    for from_key, _to, fx, fy in _CONNECTIONS:
        validate_connection(rooms[from_key], {"from_x": fx, "from_y": fy})
    for room_key, persona, x, y, _hp, _defense, _atk in NPC_SEEDS:
        validate_persona(persona)
        validate_npc_placement(rooms[room_key], x, y)

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

    for room_key, persona, x, y, hp, defense, atk in NPC_SEEDS:
        session.add(_npc_row(persona, models[room_key].id, x, y, hp, defense, atk))

    await session.commit()
    return models["default"]


async def _ensure_enemy_defs(session) -> None:
    """Synchronize authored respawnable definitions by stable numeric id."""
    for data in ENEMY_DEFS:
        row = await session.get(EnemyDef, data["id"])
        if row is None:
            session.add(EnemyDef(**data))
            continue
        for key, value in data.items():
            if key != "id":
                setattr(row, key, value)


async def seed_oakrun_world(session) -> Room:
    """Insert Oakrun + its north road and return the player starting room.

    The operation also upgrades a local prototype database safely: old room
    rows remain intact, while characters saved in those unreachable demo rooms
    are moved to Oakrun with no preferred tile so login chooses a valid spawn.
    """
    rooms = OAKRUN_ROOMS
    _validate_oakrun_content()

    await _ensure_enemy_defs(session)

    legacy_rooms = (await session.execute(
        select(Room).where(Room.name.in_((DEFAULT_ROOM["name"], SECOND_ROOM["name"])))
    )).scalars().all()
    legacy_ids = {room.id for room in legacy_rooms}

    models = {
        key: Room(
            content_id=data.get("id"),
            name=data["name"], width=data["width"], height=data["height"],
            terrain=data["terrain"], objects=data["objects"],
            spawn_points=data["spawn_points"], enemy_spawns=data["enemy_spawns"],
        )
        for key, data in rooms.items()
    }
    session.add_all(models.values())
    await session.flush()

    for from_key, to_key, fx, fy in _OAKRUN_CONNECTIONS:
        session.add(RoomConnection(
            from_room_id=models[from_key].id,
            to_room_id=models[to_key].id,
            from_x=fx,
            from_y=fy,
        ))

    for room_key, persona, x, y, hp, defense, atk in OAKRUN_NPC_SEEDS:
        session.add(_npc_row(persona, models[room_key].id, x, y, hp, defense, atk))

    if legacy_ids:
        players = (await session.execute(
            select(PlayerRow).where(PlayerRow.room_id.in_(legacy_ids))
        )).scalars().all()
        for player in players:
            player.room_id = models[_OAKRUN_CONTENT["start_room"]].id
            player.x = None
            player.y = None

    await session.commit()
    return models[_OAKRUN_CONTENT["start_room"]]


def _npc_row(persona: dict, room_id: int, x: int, y: int, hp: int, defense: int, attack_damage: int) -> NPCRow:
    return NPCRow(
        room_id=room_id,
        name=persona["name"],
        x=x, y=y,
        hp=hp, max_hp=hp,
        defense=defense, attack_damage=attack_damage,
        disposition=persona["disposition"],
        persona=persona,
        memory=[],
    )


async def _insert_npc_seeds(session, seeds, key_to_name, *, existing_ids=()) -> int:
    """Insert missing individuals from one authored seed group.

    Persona ids are stable content identity. A dead row still counts as
    existing, so a restart never resurrects someone merely because they died.
    """
    inserted = 0
    existing_ids = set(existing_ids)
    for room_key, persona, x, y, hp, defense, atk in seeds:
        validate_persona(persona)
        if persona["id"] in existing_ids:
            continue
        room = (await session.execute(
            select(Room).where(Room.name == key_to_name[room_key]))).scalars().first()
        if room is not None:
            session.add(_npc_row(persona, room.id, x, y, hp, defense, atk))
            existing_ids.add(persona["id"])
            inserted += 1
    return inserted


def _persona_id(row: NPCRow) -> str | None:
    return row.persona.get("id") if isinstance(row.persona, dict) else None


async def seed_npcs_if_missing(session) -> None:
    """Backfill missing individuals and refresh authored persona knowledge.

    Position, wounds, disposition, memory, and party ownership remain
    play-owned. Voice, relationships, knowledge, and art remain content-owned.
    """
    existing_rows = (await session.execute(select(NPCRow))).scalars().all()
    rows_by_persona_id = {_persona_id(row): row for row in existing_rows}
    existing_ids = set(rows_by_persona_id)
    for _room_key, persona, *_rest in [*NPC_SEEDS, *OAKRUN_NPC_SEEDS]:
        validate_persona(persona)
        row = rows_by_persona_id.get(persona["id"])
        if row is not None:
            row.name = persona["name"]
            # Whole JSON assignment is required for SQLAlchemy change tracking.
            row.persona = dict(persona)
    inserted = 0
    inserted += await _insert_npc_seeds(
        session,
        NPC_SEEDS,
        {"default": DEFAULT_ROOM["name"], "second": SECOND_ROOM["name"]},
        existing_ids=existing_ids,
    )
    existing_ids.update(persona["id"] for _, persona, *_ in NPC_SEEDS)
    inserted += await _insert_npc_seeds(
        session,
        OAKRUN_NPC_SEEDS,
        _OAKRUN_ROOM_NAMES,
        existing_ids=existing_ids,
    )
    await session.commit()


async def reset_npcs(session) -> None:
    """DEV: wipe all individual state and restore every present seed group."""
    await session.execute(NPCRow.__table__.delete())
    await _insert_npc_seeds(
        session,
        NPC_SEEDS,
        {"default": DEFAULT_ROOM["name"], "second": SECOND_ROOM["name"]},
    )
    await _insert_npc_seeds(
        session,
        OAKRUN_NPC_SEEDS,
        _OAKRUN_ROOM_NAMES,
    )
    await session.commit()


# --- the starter item pool (docs/LOOT.md) -------------------------------------
# Hand-authored items in exactly the format the premium-LLM generator emits
# later — every row passes items.validate_item on the way in, so seeds and
# generated items live under one contract. The pool deliberately exercises
# EVERY effect atom (stat_mod timed + untimed, restore_hp, damage) and every
# type x rarity corner that exists, so no atom ships without a live item
# using it. Numbers sit inside items.RARITY_CAPS; combat scale reference:
# player attack 30 / hp 100, enemy hp 4-8.

STARTER_ITEMS = [
    # -- common ---------------------------------------------------------------
    {"name": "Health Potion", "rarity": "common", "type": "consumable",
     "description": "A stoppered vial of red liquid. Tastes like copper, works like magic.",
     "art": {"kind": "emoji", "value": "🧪"},
     "payload": {"effects": [{"kind": "restore_hp", "amount": 12}]}},
    {"name": "Traveler's Bread", "rarity": "common", "type": "consumable",
     "description": "Dense, dry, and dependable. A bite steadies the hands.",
     "art": {"kind": "emoji", "value": "🍞"},
     "payload": {"effects": [{"kind": "restore_hunger", "amount": 25},
                             {"kind": "restore_hp", "amount": 4}]}},
    {"name": "Wheel of Cheese", "rarity": "common", "type": "consumable",
     "description": "Waxed rind, sharp heart. It has outlasted three owners already.",
     "art": {"kind": "emoji", "value": "🧀"},
     "payload": {"effects": [{"kind": "restore_hunger", "amount": 20}]}},
    {"name": "Throwing Stone", "rarity": "common", "type": "throwable",
     "description": "Fits the palm like it was quarried for spite.",
     "art": {"kind": "emoji", "value": "🪨"},
     "payload": {"throw_range": 4, "area": {"shape": "radius", "size": 0},
                 "effects": [{"kind": "damage", "amount": 2}]}},
    {"name": "Bomb", "rarity": "common", "type": "throwable",
     "description": "A fist of black powder with a short fuse and shorter patience.",
     "art": {"kind": "emoji", "value": "💣"},
     "payload": {"throw_range": 4, "area": {"shape": "radius", "size": 1},
                 "effects": [{"kind": "damage", "amount": 3}]}},
    {"name": "Rusty Dagger", "rarity": "common", "type": "weapon",
     "description": "The edge is honest even if the metal isn't.",
     "art": {"kind": "emoji", "value": "🗡️"},
     "payload": {"damage": 32, "range": 1}},
    {"name": "Leather Cap", "rarity": "common", "type": "wearable",
     "description": "Smells of old rain. Keeps your skull where you left it.",
     "art": {"kind": "emoji", "value": "🪖"},
     "payload": {"effects": [{"kind": "stat_mod", "stat": "defense", "amount": 1}]}},
    {"name": "Wooden Buckler", "rarity": "common", "type": "wearable",
     "description": "A round of scarred oak. It has stopped worse than you'd think.",
     "art": {"kind": "emoji", "value": "🛡️"},
     "payload": {"effects": [{"kind": "stat_mod", "stat": "defense", "amount": 2}]}},

    # -- rare -----------------------------------------------------------------
    {"name": "Greater Health Potion", "rarity": "rare", "type": "consumable",
     "description": "The red of it glows faintly. Wounds close like doors.",
     "art": {"kind": "emoji", "value": "⚗️"},
     "payload": {"effects": [{"kind": "restore_hp", "amount": 35}]}},
    {"name": "Potion of Fury", "rarity": "rare", "type": "consumable",
     "description": "Drink, and for a minute the world slows down to be hit.",
     "art": {"kind": "emoji", "value": "🧉"},
     "payload": {"effects": [
         {"kind": "stat_mod", "stat": "attack_damage", "amount": 8, "duration_s": 60}]}},
    {"name": "Hunter's Bow", "rarity": "rare", "type": "weapon",
     "description": "Yew and sinew, patient as winter. Strikes from across the hall.",
     "art": {"kind": "emoji", "value": "🏹"},
     "payload": {"damage": 34, "range": 4}},
    {"name": "Steel Sword", "rarity": "rare", "type": "weapon",
     "description": "Unremarkable, well-kept, and better than everything before it.",
     "art": {"kind": "emoji", "value": "⚔️"},
     "payload": {"damage": 42, "range": 1}},
    {"name": "Hearty Stew", "rarity": "rare", "type": "consumable",
     "description": "Still steaming, somehow. Whoever made it knew what hunger costs.",
     "art": {"kind": "emoji", "value": "🍲"},
     "payload": {"effects": [{"kind": "restore_hunger", "amount": 55},
                             {"kind": "restore_hp", "amount": 15}]}},
    {"name": "Poison Flask", "rarity": "rare", "type": "throwable",
     "description": "Green fog on impact — flesh sickens and armor means less.",
     "art": {"kind": "emoji", "value": "☠️"},
     "payload": {"throw_range": 4, "area": {"shape": "radius", "size": 1},
                 "effects": [
                     {"kind": "damage", "amount": 2},
                     {"kind": "stat_mod", "stat": "defense", "amount": -2, "duration_s": 120}]}},

    # -- legendary ------------------------------------------------------------
    {"name": "Phoenix Elixir", "rarity": "legendary", "type": "consumable",
     "description": "Liquid dawn. Whatever you were before, you are whole now.",
     "art": {"kind": "emoji", "value": "🔥"},
     "payload": {"effects": [{"kind": "restore_hp", "amount": 100}]}},
    {"name": "Dragonfang Blade", "rarity": "legendary", "type": "weapon",
     "description": "Still warm. The forge that made it had a heartbeat.",
     "art": {"kind": "emoji", "value": "🐉"},
     "payload": {"damage": 60, "range": 1}},
    {"name": "Titan's Aegis", "rarity": "legendary", "type": "wearable",
     "description": "A breastplate sized for something larger. It makes room for you.",
     "art": {"kind": "emoji", "value": "⚜️"},
     "payload": {"effects": [
         {"kind": "stat_mod", "stat": "defense", "amount": 5},
         {"kind": "stat_mod", "stat": "max_hp", "amount": 20}]}},
    {"name": "Feast of the Last King", "rarity": "legendary", "type": "consumable",
     "description": "A banquet folded impossibly into one golden bite. You rise from it renewed.",
     "art": {"kind": "emoji", "value": "🍗"},
     "payload": {"effects": [{"kind": "restore_hunger", "amount": 100},
                             {"kind": "restore_hp", "amount": 40}]}},
    {"name": "Starfall Grenade", "rarity": "legendary", "type": "throwable",
     "description": "A sliver of night sky in a glass shell. The landing is loud.",
     "art": {"kind": "emoji", "value": "💥"},
     "payload": {"throw_range": 5, "area": {"shape": "radius", "size": 2},
                 "effects": [{"kind": "damage", "amount": 12}]}},
]


async def seed_items_if_missing(session) -> None:
    """Backfill the global item pool when the items table has NEVER held a row
    (the seed_npcs_if_missing rhythm). A pool the players have grown with LLM
    inventions has rows — reseeding must never dilute or duplicate it. When
    the STARTER_ITEMS list itself changes, delete the db and restart: the
    data is proof-of-concept and disposable by policy (no migration code for
    content that hasn't earned it)."""
    from backend.item_store import insert_item, pool_is_empty
    if not await pool_is_empty(session):
        return
    for data in STARTER_ITEMS:
        await insert_item(session, data, origin="seed")
    await session.commit()


async def get_or_seed_default_room(session) -> Room:
    """Synchronize authored Oakrun definitions and return its start room.

    Only definition-owned fields are updated. NPC lives, player positions,
    dialogue memory, and object-instance state remain database-owned.
    """
    _validate_oakrun_content()
    start_content_id = OAKRUN_ROOM["id"]
    existing = (await session.execute(
        select(Room).where(
            (Room.content_id == start_content_id) | (Room.name == OAKRUN_ROOM["name"])
        ))).scalars().first()
    if existing is None:
        return await seed_oakrun_world(session)

    rows_by_key = {}
    for key, data in _OAKRUN_CONTENT["rooms"].items():
        row = (await session.execute(
            select(Room).where(
                (Room.content_id == data["id"]) | (Room.name == data["name"])
            ))).scalars().first()
        if row is None:
            row = Room(
                content_id=data["id"], name=data["name"], width=data["width"],
                height=data["height"], terrain=data["terrain"], objects=data["objects"],
                spawn_points=data["spawn_points"], enemy_spawns=data.get("enemy_spawns", []),
            )
            session.add(row)
        else:
            row.content_id = data["id"]
            row.name = data["name"]
            row.width = data["width"]
            row.height = data["height"]
            row.terrain = data["terrain"]
            row.objects = data.get("objects", [])
            row.spawn_points = data["spawn_points"]
            row.enemy_spawns = data.get("enemy_spawns", [])
        rows_by_key[key] = row

    await session.flush()
    # Authored outgoing graph edges are source-controlled as one set. Rebuild
    # them atomically so removing or moving a door cannot leave a stale exit.
    await session.execute(delete(RoomConnection).where(
        RoomConnection.from_room_id.in_([row.id for row in rows_by_key.values()])
    ))
    for from_key, to_key, x, y in _OAKRUN_CONNECTIONS:
        source = rows_by_key[from_key]
        target = rows_by_key[to_key]
        session.add(RoomConnection(
            from_room_id=source.id, to_room_id=target.id, from_x=x, from_y=y,
        ))

    await _ensure_enemy_defs(session)
    await seed_npcs_if_missing(session)
    await session.commit()
    return rows_by_key[_OAKRUN_CONTENT["start_room"]]
