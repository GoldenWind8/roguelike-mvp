"""Hand-authored seed data (the format an LLM will emit later).

ENEMY_DEFS is the reusable enemy catalog (stable ids). Rooms reference enemies
by id and store only their placement — stats are loaded from enemy_defs.

DEFAULT_ROOM is a 10x10 pillared hall that drops into the running 10x10
frontend. Players ENTER through the south door and spawn clustered around it;
a far door (north) and the south door both link to SECOND_ROOM, and walking
through them traverses to it.

Terrain is an ASCII grid (one char per tile, see TileType):
    #  wall      .  floor      +  door      O  portal
"""
from sqlalchemy import select

from backend.persona import validate_persona
from backend.room_validation import (
    validate_connection,
    validate_enemy_refs,
    validate_npc_placement,
    validate_room,
)
from backend.models import EnemyDef, NPCRow, Room, RoomConnection


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
        "###+###",
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
THIRD_ROOM = {
    "name": "The Den",
    "width": 9,
    "height": 7,
    "terrain": [
        "#########",
        "#.......#",
        "#....####",
        "#.......#",
        "#......##",
        "#.......#",
        "####+####",
    ],
    # Two spawns so a pair can co-locate here (capacity = spawn count).
    "spawn_points": [[3, 5], [4, 5]],
    "enemy_spawns": [{"enemy_id": 3, "x": 4, "y": 3}],
    "objects": [],
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

LILYA_PERSONA = {
    "id": "lilya-den",
    "name": "Lilya",
    "role": "resident of the forgotten den",
    "persona": (
        "A peculiar but kind young woman who has made a quiet home in an "
        "abandoned underground den. She cannot remember how she arrived there, "
        "only that the cold stone walls and endless silence felt familiar from "
        "the very beginning. She seems to have lived there for far longer than "
        "a normal person should, yet she never questions it. "
        "Lilya is friendly and curious about the outside world, but crowds, "
        "noise, and bright places make her deeply uncomfortable. She finds "
        "peace in darkness and solitude, treating the strange sounds and "
        "creatures of the dungeon as ordinary neighbors. "
        "She has an unusual fondness for boiled eggs and considers salads one "
        "of the strangest foods ever invented."
    ),
    "drives": [
        "protect the quiet and comfort of her den",
        "learn about the outside world through travelers",
        "understand the mystery of how she came underground",
        "keep her small home exactly as she likes it",
    ],
    "disposition": "friendly",
    "canned": [
        "Oh, hello. I don't get many visitors. Not looking like me, anyway.",
        "Would you like a boiled egg? I made too many. That happens often.",
        "A salad? Here? That's brave. I don't trust food that looks happier than me.",
        "People always ask why I stay here. I suppose I forgot to leave.",
        "The outside is very loud. Everyone talks, footsteps are everywhere... "
        "Don't worry about the noises. Most things down here are quite polite.",
        "I've been here a long time. I think. Time behaves strangely underground.",
    ],
    "party_policy": (
        "Will gladly talk with travelers and share stories, but refuses to "
        "leave the den. The thought of abandoning her familiar home frightens "
        "her more than the dangers outside. She prefers to welcome visitors "
        "rather than become one herself."
    ),
}
# (room_key, x, y, hp, defense) — placement is validated against the room
# like every other seeded thing. Stats are modest: he is a caretaker, not a
# combatant, but he IS an actor and can be hurt. Lilya doesn't have a lot of health, she's just a girl.
NPC_SEEDS = [
    ("second", GORRIK_PERSONA, 5, 2, 20, 1),
    ("third", LILYA_PERSONA, 7, 1, 12, 1)
]


# Edges of the world graph: (from_room_key, to_room_key, from_x, from_y).
# Resolved to real room ids at seed time once the rows have been flushed.
_CONNECTIONS = [
    ("default", "second", 4, 0),   # far door
    ("default", "second", 4, 9),   # entry door
    ("second", "default", 0, 2),   # door back to default
    ("second", "third", 3, 0),     # door in second room to third room
    ("third", "second", 4, 6)      # door in third room to second
]


async def seed_default_rooms(session) -> Room:
    """Validate, insert, and link the enemy catalog + the initial rooms.
    Returns the default Room (the one the game starts in)."""
    rooms = {"default": DEFAULT_ROOM, "second": SECOND_ROOM, "third": THIRD_ROOM}
    known_enemy_ids = {d["id"] for d in ENEMY_DEFS}

    # Validate everything before touching the DB — fail fast, fail loud.
    for data in rooms.values():
        validate_room(data)
        validate_enemy_refs(data, known_enemy_ids)
    for from_key, _to, fx, fy in _CONNECTIONS:
        validate_connection(rooms[from_key], {"from_x": fx, "from_y": fy})
    for room_key, persona, x, y, _hp, _defense in NPC_SEEDS:
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

    for room_key, persona, x, y, hp, defense in NPC_SEEDS:
        session.add(_npc_row(persona, models[room_key].id, x, y, hp, defense))

    await session.commit()
    return models["default"]


def _npc_row(persona: dict, room_id: int, x: int, y: int, hp: int, defense: int) -> NPCRow:
    return NPCRow(
        room_id=room_id,
        name=persona["name"],
        x=x, y=y,
        hp=hp, max_hp=hp,
        defense=defense, attack_damage=0,
        disposition=persona["disposition"],
        persona=persona,
        memory=[],
    )


async def seed_npcs_if_missing(session) -> None:
    """Backfill for databases seeded before the npcs table existed. Runs only
    when the table has NEVER held a row — an empty-because-everyone-died table
    still has rows (is_alive=False), and individuals must stay dead."""
    existing = (await session.execute(select(NPCRow))).scalars().first()
    if existing is not None:
        return
    key_to_name = {"default": DEFAULT_ROOM["name"], "second": SECOND_ROOM["name"]}
    for room_key, persona, x, y, hp, defense in NPC_SEEDS:
        validate_persona(persona)
        room = (await session.execute(
            select(Room).where(Room.name == key_to_name[room_key]))).scalars().first()
        if room is not None:
            session.add(_npc_row(persona, room.id, x, y, hp, defense))
    await session.commit()


async def get_or_seed_default_room(session) -> Room:
    """Idempotent startup helper: return the default room, seeding the world on
    first boot if the rooms table is empty (Decision A — zero-touch startup)."""
    existing = (await session.execute(
        select(Room).where(Room.name == DEFAULT_ROOM["name"]))).scalars().first()
    if existing is None:
        return await seed_default_rooms(session)
    await seed_npcs_if_missing(session)
    return existing
