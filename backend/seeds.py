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
from backend.carriage_store import (
    DRAZNA_CARRIAGE_ACTIVATION_GROUP,
    ensure_carriage_stop,
)
from backend.living_world_content import load_living_world_content
from backend.persona import validate_persona
from backend.room_validation import (
    validate_connection,
    validate_enemy_refs,
    validate_npc_placement,
    validate_room,
)
from backend.models import (
    CarriageRoute,
    CarriageStop,
    EnemyDef,
    FrontierExit,
    FrontierNode,
    NPCRow,
    PlayerRow,
    Room,
    RoomConnection,
)


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
_DRAZNA_CONTENT = load_region("world/drazna/region.json")
_ROUVRAY_CONTENT = load_region("world/rouvray/region.json")
AUTHORED_REGIONS = {
    "oakrun": _OAKRUN_CONTENT,
    "drazna": _DRAZNA_CONTENT,
    "rouvray": _ROUVRAY_CONTENT,
}
SECONDARY_REGIONS = (_DRAZNA_CONTENT, _ROUVRAY_CONTENT)
_ALL_AUTHORED_ROOMS = {
    room_id: room
    for region in AUTHORED_REGIONS.values()
    for room_id, room in region["rooms"].items()
}
_ALL_AUTHORED_ROOM_NAMES = {
    room_id: data["name"] for room_id, data in _ALL_AUTHORED_ROOMS.items()
}
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
_LIVING_WORLD_CONTENT = load_living_world_content()


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
SECONDARY_AUTHORED_NPC_SEEDS = []
for _entry in _AUTHORED_NPCS.values():
    _spawn = _entry["spawn"]
    _stats = _entry["stats"]
    _seed = (
        _spawn["room"], _persona_from_content(_entry), _spawn["x"], _spawn["y"],
        _stats["hp"], _stats["defense"], _stats["attack_damage"],
    )
    if _spawn["region"] == "oakrun":
        OAKRUN_NPC_SEEDS.append(_seed)
    else:
        SECONDARY_AUTHORED_NPC_SEEDS.append(_seed)


_LIVING_NPC_PLACEMENTS = {
    "mara-vey": ("drazna_palace_still_water", (8, 4)),
    "ilya-sorn": ("drazna_crown_sluice", (6, 9)),
    "nera-bell": ("drazna_house_of_names", (8, 7)),
    "olek-var": ("drazna_mud_crown", (11, 7)),
    "pava-mirek": ("drazna_walking_ward", (8, 7)),
    "vasko-mirek": ("drazna_undertide", (11, 9)),
    "vesna-korr": ("drazna_dry_dock", (6, 8)),
    "alin-vey": ("drazna_palace_still_water", (6, 8)),
    "jory-rusk": ("oakrun_fieldsite_verge", (5, 10)),
    "sabine-vauclair": ("bellifont", (5, 5)),
    "matthieu-orne": ("orison_fields", (10, 5)),
    "lina-pell": ("drazna_lantern_quays", (8, 9)),
}

_LIVING_NPC_VOICES = {
    "mara-vey": (
        "Regal without ceremony, exhausted by ceremonies that outlived the wards they named. "
        "She speaks in exact decisions and treats every euphemism as a leak in a wall.",
        ["A crown is only useful if it keeps rain off someone.", "Drazna recorded the wound. We did not confess to making it."],
        "The remaining wards and their people are her party; she will not abandon them.",
        "queen_mara_vey",
    ),
    "ilya-sorn": (
        "A young floodwarden whose hands never stop measuring pressure, distance, and blame. "
        "He is direct until guilt makes him suddenly formal.",
        ["Water keeps no secret. People build gates and call that secrecy.", "If the lower gauge rises again, run uphill before you ask why."],
        "He will not leave while the sluices can still be repaired.",
        "ilya_sorn",
    ),
    "nera-bell": (
        "A patient archivist who believes an omitted name is a second death. "
        "She corrects comforting falsehoods softly and dates every certainty.",
        ["First recorded is not first begun. Write that down exactly.", "The dead do not need praise. They need their names restored."],
        "The House of Names is her vigil and she will not desert it.",
        "nera_bell",
    ),
    "olek-var": (
        "A salvage captain with a dockworker's humor and a priest's respect for drowned rooms. "
        "He prices danger honestly and dislikes heroes who make crews carry their bodies home.",
        ["Everything below the tide belongs to someone. Mostly the dead.", "Coin first, rope second, courage a distant third."],
        "He travels only with a contracted salvage crew.",
        "olek_var",
    ),
    "pava-mirek": (
        "A master roofwright who reads buildings as other people read faces. "
        "Blunt, maternal, and furious at officials who call preventable collapse weather.",
        ["That beam did not fail. Someone stopped listening to it.", "Bring me facts about my brother, not another theory."],
        "Her crews and the Walking Ward keep her in Drazna.",
        "pava_mirek",
    ),
    "vasko-mirek": (
        "An Undertide diver made wrong-footed by too many descents, laconic and alert to sounds beneath floors. "
        "He jokes when frightened and refuses to explain where he learned certain drowned names.",
        ["There are doors underwater that open toward dry rooms.", "Pava will hit me before she hugs me. Fair order."],
        "He may join someone willing to descend carefully and keep faith with the drowned.",
        "vasko_mirek",
    ),
    "vesna-korr": (
        "A night-route keeper whose calm comes from having already imagined the axle breaking. "
        "She gives warnings as practical gifts and never calls a road safe, only passable.",
        ["Low Lantern leaves when the third wick gutters.", "A road can be open and still mean you harm."],
        "She travels with her carriage service, not as an ordinary follower.",
        "vesna_korr",
    ),
    "alin-vey": (
        "A reformist heir who speaks too quickly when angry and too carefully near the Crown. "
        "He wants truth made public but has not yet learned what panic costs.",
        ["Silence did not save the drowned ward. It only saved reputations.", "My mother calls delay prudence. The water calls it time."],
        "He will not leave while the Crown can still be changed from within.",
        "alin_vey",
    ),
    "jory-rusk": (
        "Driver of the Grey Heron, broad-shouldered and superstitious about bells. "
        "He remembers passengers by luggage, lies by wheel-ruts, and danger by what horses refuse.",
        ["Grey Heron goes where the road admits it exists.", "Never trust a milestone cleaner than your wheels."],
        "He belongs to his carriage route and will not join a wandering party.",
        "carriage_driver",
    ),
    "sabine-vauclair": (
        "A Rouvrain field physician with immaculate manners and mud permanently under her cuffs. "
        "She separates observed symptoms from rumor and kindness from reassurance.",
        ["I can promise attention. A cure would be a lie.", "Drazna kept records. That is not the same as causing what they recorded."],
        "Her patients determine her road; she will not become a mercenary companion.",
        "basil",
    ),
    "matthieu-orne": (
        "A bell-road driver who speaks to his team more readily than to passengers. "
        "Dryly devout, he hears omens in bad bearings but trusts maintenance over prayer.",
        ["Bell and Reed leaves at dawn, unless the bells object.", "A named stop is a promise that somebody can find you again."],
        "He travels only with the Bell and Reed service.",
        "carriage_driver",
    ),
    "lina-pell": (
        "A Draznan refugee who rebuilt a ruined wagon into the Mudwheel carriage. "
        "Fiercely hospitable, suspicious of borders, and unwilling to let strangers simplify what happened to her home.",
        ["We were people before we were evidence.", "Mudwheel takes anyone who helps push when the road sinks."],
        "She may join briefly when protecting refugees or reopening a stranded road demands it.",
        "carriage_driver",
    ),
}

_LIVING_NPC_RELATIONSHIPS = {
    "mara-vey": [
        {
            "npc_id": "alin-vey",
            "name": "Alin Vey",
            "connection": (
                "Her son and political opponent inside the same palace. "
                "She trusts his conscience more than his timing."
            ),
        },
        {
            "npc_id": "rada-velic",
            "name": "Rada Velic",
            "connection": (
                "The floodwarden whose unsentimental reports Mara believes "
                "even when the court does not."
            ),
        },
        {
            "npc_id": "nera-bell",
            "name": "Nera Bell",
            "connection": (
                "The archivist preserving names Mara delayed publishing; "
                "their respect survives a grievance neither will soften."
            ),
        },
    ],
    "ilya-sorn": [
        {
            "npc_id": "rada-velic",
            "name": "Rada Velic",
            "connection": (
                "His severe mentor, first defender, and the person most "
                "likely to recognize what he has concealed."
            ),
        },
        {
            "npc_id": "nera-bell",
            "name": "Nera Bell",
            "connection": (
                "An archive contact who turns his pressure readings into "
                "records the Crown cannot quietly revise."
            ),
        },
        {
            "npc_id": "mara-vey",
            "name": "Mara Vey",
            "connection": (
                "His queen and direct authority at the sluice; her trust "
                "makes disobedience harder, not easier."
            ),
        },
    ],
    "nera-bell": [
        {
            "npc_id": "luka-nen",
            "name": "Luka Nen",
            "connection": (
                "A surviving census witness whose five omitted workmates "
                "could force the public roll open."
            ),
        },
        {
            "npc_id": "alin-vey",
            "name": "Alin Vey",
            "connection": (
                "A reformist ally who wants her evidence published faster "
                "than she believes it can safely survive."
            ),
        },
        {
            "npc_id": "lina-pell",
            "name": "Lina Pell",
            "connection": (
                "The Mudwheel driver who carries household bundles uphill "
                "without turning absence into an official death."
            ),
        },
    ],
    "olek-var": [
        {
            "npc_id": "vasko-mirek",
            "name": "Vasko Mirek",
            "connection": (
                "His most gifted diver and the missing crewman whose return "
                "could expose Olek's last unsafe contract."
            ),
        },
        {
            "npc_id": "pava-mirek",
            "name": "Pava Mirek",
            "connection": (
                "A roofwright who bargains rescue rope against his salvage "
                "gear and never lets him price her brother's life."
            ),
        },
        {
            "npc_id": "teo-latch",
            "name": "Teo Latch",
            "connection": (
                "A useful fence whose maps Olek buys only after checking "
                "which names have been scraped away."
            ),
        },
    ],
    "pava-mirek": [
        {
            "npc_id": "vasko-mirek",
            "name": "Vasko Mirek",
            "connection": (
                "Her younger brother, a brilliant diver, and the one person "
                "whose reckless promises still frighten her."
            ),
        },
        {
            "npc_id": "sima-dren",
            "name": "Sima Dren",
            "connection": (
                "Her apprentice and chosen heir to the seven-fold brace "
                "marks; Pava is proud enough to sound perpetually angry."
            ),
        },
        {
            "npc_id": "olek-var",
            "name": "Olek Var",
            "connection": (
                "A salvage captain she hires for impossible lifts and "
                "blames for treating danger as an honest price."
            ),
        },
    ],
    "vasko-mirek": [
        {
            "npc_id": "pava-mirek",
            "name": "Pava Mirek",
            "connection": (
                "His older sister, rescuer, and fiercest critic; he fears "
                "her relief more than her anger."
            ),
        },
        {
            "npc_id": "luka-nen",
            "name": "Luka Nen",
            "connection": (
                "A dry-route partner who kept counting survivors while "
                "Vasko followed the Gate Seven signals."
            ),
        },
        {
            "npc_id": "olek-var",
            "name": "Olek Var",
            "connection": (
                "His captain and creditor, who supplied the line Vasko cut "
                "when the official route stopped being safe."
            ),
        },
    ],
    "vesna-korr": [
        {
            "npc_id": "teo-latch",
            "name": "Teo Latch",
            "connection": (
                "A broker who sells her useful routes and dangerous reasons "
                "to doubt who else bought them."
            ),
        },
        {
            "npc_id": "lina-pell",
            "name": "Lina Pell",
            "connection": (
                "A fellow driver whose visible Mudwheel runs cover the "
                "quieter journeys Vesna cannot put on a timetable."
            ),
        },
        {
            "npc_id": "drina-sable",
            "name": "Drina Sable",
            "connection": (
                "The innkeeper who holds a warm back room for passengers "
                "Vesna brings in without names."
            ),
        },
    ],
    "alin-vey": [
        {
            "npc_id": "mara-vey",
            "name": "Mara Vey",
            "connection": (
                "His mother, queen, and strongest political obstacle; he "
                "mistakes their shared purpose for agreement."
            ),
        },
        {
            "npc_id": "nera-bell",
            "name": "Nera Bell",
            "connection": (
                "The archivist he needs as an ally and keeps endangering by "
                "demanding a public answer before her evidence is secure."
            ),
        },
        {
            "npc_id": "luka-nen",
            "name": "Luka Nen",
            "connection": (
                "A witness Alin wants heard at court, though Luka refuses to "
                "become a symbol before the dry-dock survivors are safe."
            ),
        },
    ],
    "lina-pell": [
        {
            "npc_id": "drina-sable",
            "name": "Drina Sable",
            "connection": (
                "The keeper of her unofficial arrival book and the only "
                "person Lina trusts to record a passenger's chosen name."
            ),
        },
        {
            "npc_id": "nera-bell",
            "name": "Nera Bell",
            "connection": (
                "The archivist receiving her uphill name bundles, sometimes "
                "after Lina has quietly changed their route."
            ),
        },
        {
            "npc_id": "vesna-korr",
            "name": "Vesna Korr",
            "connection": (
                "A night-route counterpart who knows which Mudwheel "
                "passengers must not appear on a public fare tally."
            ),
        },
    ],
}


def _living_persona(profile: dict) -> dict:
    voice, canned, party_policy, art_id = _LIVING_NPC_VOICES[profile["id"]]
    beliefs = []
    for reference in profile["belief_refs"]:
        rumor = _LIVING_WORLD_CONTENT.rumors[reference["rumor_id"]]
        belief = next(
            item for item in rumor["beliefs"]
            if item["id"] == reference["belief_id"]
        )
        beliefs.append(belief["claim"])
    persona = {
        "id": profile["id"],
        "name": profile["name"],
        "role": profile["role"],
        "persona": voice,
        "drives": [
            goal["desire"] for goal in profile["private_goals"][:3]
        ],
        "knowledge": beliefs,
        "relationships": [
            dict(relationship)
            for relationship in _LIVING_NPC_RELATIONSHIPS.get(
                profile["id"],
                (),
            )
        ],
        "disposition": (
            "friendly" if profile["id"] in {"sabine-vauclair", "lina-pell"}
            else "neutral"
        ),
        "canned": canned,
        "party_policy": party_policy,
        "art_id": art_id,
    }
    if profile["id"] in {"vasko-mirek", "lina-pell"}:
        persona["grants"] = ["join_party"]
    return persona


LIVING_NPC_SEEDS = []
for _profile in _LIVING_WORLD_CONTENT.npc_profiles.values():
    if _profile["id"] not in _LIVING_NPC_PLACEMENTS:
        continue
    _room_id, (_x, _y) = _LIVING_NPC_PLACEMENTS[_profile["id"]]
    _hp = 32 if _profile["kind"] == "official" else 26
    LIVING_NPC_SEEDS.append((
        _room_id,
        _living_persona(_profile),
        _x,
        _y,
        _hp,
        2,
        3 if _profile["id"] in {"ilya-sorn", "olek-var", "vasko-mirek"} else 1,
    ))


def _validate_living_npc_seeds() -> None:
    for room_key, persona, x, y, *_stats in LIVING_NPC_SEEDS:
        validate_persona(persona)
        validate_npc_placement(_ALL_AUTHORED_ROOMS[room_key], x, y)


def _validate_secondary_authored_npc_seeds() -> None:
    for room_key, persona, x, y, *_stats in SECONDARY_AUTHORED_NPC_SEEDS:
        validate_persona(persona)
        if room_key not in _ALL_AUTHORED_ROOMS:
            raise ValueError(
                f"Core NPC {persona['id']!r} references unknown authored "
                f"room {room_key!r}"
            )
        validate_npc_placement(_ALL_AUTHORED_ROOMS[room_key], x, y)


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

# Temporary development-only access while Drazna is playtested as an authored
# chapter. This is deliberately an ordinary pair of RoomConnection rows, not
# a FrontierExit or FrontierNode: it must not count as discovering Drazna,
# consume its procedural gateway, or unlock its carriage stop.
TEMPORARY_OAKRUN_DRAZNA_BRIDGE_ENABLED = True
TEMPORARY_OAKRUN_DRAZNA_BRIDGE = (
    ("oakrun_fieldsite_verge", "drazna_lantern_quays", 16, 1),
    ("drazna_lantern_quays", "oakrun_fieldsite_verge", 0, 9),
)


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


def _validate_authored_region(content: dict) -> None:
    """Validate one authored kingdom without assuming it owns NPC spawns."""
    known_enemy_ids = {definition["id"] for definition in ENEMY_DEFS}
    for data in content["rooms"].values():
        validate_room(data)
        validate_enemy_refs(data, known_enemy_ids)
    connection_tiles: set[tuple[str, int, int]] = set()
    for connection in content["connections"]:
        from_key = connection["from"]
        tile = (from_key, connection["x"], connection["y"])
        if tile in connection_tiles:
            raise ValueError(
                f"Region {content['id']!r} repeats a connection at "
                f"({connection['x']}, {connection['y']}) in {from_key!r}"
            )
        connection_tiles.add(tile)
        validate_connection(
            content["rooms"][from_key],
            {"from_x": connection["x"], "from_y": connection["y"]},
        )


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

    await _sync_authored_frontier_exits(session, models)
    await _sync_secondary_regions(session)
    await _sync_temporary_oakrun_drazna_bridge(session)
    await ensure_carriage_stop(
        session,
        stop_key="stop:oakrun-exchange",
        room_id=models[_OAKRUN_CONTENT["start_room"]].id,
        biome="amberfall_fields",
        world_minute=0,
        public_name="Oakrun Exchange",
        metadata={"authored": True},
    )
    await _sync_drazna_carriage_topology(session)

    for room_key, persona, x, y, hp, defense, atk in OAKRUN_NPC_SEEDS:
        session.add(_npc_row(persona, models[room_key].id, x, y, hp, defense, atk))
    _validate_secondary_authored_npc_seeds()
    _validate_living_npc_seeds()
    core_ids = {persona["id"] for _, persona, *_ in OAKRUN_NPC_SEEDS}
    await _insert_npc_seeds(
        session,
        SECONDARY_AUTHORED_NPC_SEEDS,
        _ALL_AUTHORED_ROOM_NAMES,
        existing_ids=core_ids,
    )
    core_ids.update(
        persona["id"] for _, persona, *_ in SECONDARY_AUTHORED_NPC_SEEDS
    )
    await _insert_npc_seeds(
        session,
        LIVING_NPC_SEEDS,
        _ALL_AUTHORED_ROOM_NAMES,
        existing_ids=core_ids,
    )

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
        content_id=persona["id"],
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
    if isinstance(row.content_id, str) and row.content_id:
        return row.content_id
    return row.persona.get("id") if isinstance(row.persona, dict) else None


async def seed_npcs_if_missing(session) -> None:
    """Backfill missing individuals and refresh authored persona knowledge.

    Position, wounds, disposition, memory, and party ownership remain
    play-owned. Voice, relationships, knowledge, and art remain content-owned.
    """
    existing_rows = (await session.execute(select(NPCRow))).scalars().all()
    rows_by_persona_id = {_persona_id(row): row for row in existing_rows}
    existing_ids = set(rows_by_persona_id)
    _validate_secondary_authored_npc_seeds()
    _validate_living_npc_seeds()
    for _room_key, persona, *_rest in [
        *NPC_SEEDS,
        *OAKRUN_NPC_SEEDS,
        *SECONDARY_AUTHORED_NPC_SEEDS,
        *LIVING_NPC_SEEDS,
    ]:
        validate_persona(persona)
        row = rows_by_persona_id.get(persona["id"])
        if row is not None:
            # Legacy rows held this identity only inside persona JSON. Keep the
            # dedicated column authoritative from the first synchronized boot.
            row.content_id = persona["id"]
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
    existing_ids.update(persona["id"] for _, persona, *_ in OAKRUN_NPC_SEEDS)
    inserted += await _insert_npc_seeds(
        session,
        SECONDARY_AUTHORED_NPC_SEEDS,
        _ALL_AUTHORED_ROOM_NAMES,
        existing_ids=existing_ids,
    )
    existing_ids.update(
        persona["id"] for _, persona, *_ in SECONDARY_AUTHORED_NPC_SEEDS
    )
    inserted += await _insert_npc_seeds(
        session,
        LIVING_NPC_SEEDS,
        _ALL_AUTHORED_ROOM_NAMES,
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
    await _insert_npc_seeds(
        session,
        SECONDARY_AUTHORED_NPC_SEEDS,
        _ALL_AUTHORED_ROOM_NAMES,
    )
    await _insert_npc_seeds(
        session,
        LIVING_NPC_SEEDS,
        _ALL_AUTHORED_ROOM_NAMES,
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
    """Seed a virgin pool, then idempotently backfill scoped authored loot.

    The generic starter pool is still inserted only when the table has never
    held a row, preserving player/LLM-grown pools. Regional definitions are a
    bounded content migration: their bundled art values are stable markers,
    so existing rows are neither replayed nor edited."""
    from backend.item_store import (
        insert_item,
        insert_missing_authored_items,
        pool_is_empty,
    )
    from backend.regional_items import DRAZNA_ITEMS

    if await pool_is_empty(session):
        for data in STARTER_ITEMS:
            await insert_item(session, data, origin="seed")
    await insert_missing_authored_items(session, DRAZNA_ITEMS, origin="seed")
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
    # Generated connections from an authored frontier door are play-owned.
    # Rebuilding the authored graph above must not sever a road players have
    # already discovered.
    await _sync_authored_frontier_exits(session, rows_by_key)
    await _sync_secondary_regions(session)
    await _sync_temporary_oakrun_drazna_bridge(session)
    await ensure_carriage_stop(
        session,
        stop_key="stop:oakrun-exchange",
        room_id=rows_by_key[_OAKRUN_CONTENT["start_room"]].id,
        biome="amberfall_fields",
        world_minute=0,
        public_name="Oakrun Exchange",
        metadata={"authored": True},
    )
    await _sync_drazna_carriage_topology(session)

    await _ensure_enemy_defs(session)
    await seed_npcs_if_missing(session)
    await session.commit()
    return rows_by_key[_OAKRUN_CONTENT["start_room"]]


async def _sync_secondary_regions(session) -> dict[str, dict[str, Room]]:
    """Upsert the distant authored kingdoms without revealing their roads.

    The rooms exist so a frontier discovery can connect atomically to a real
    gateway. Their external gateway doors remain disconnected until the
    rising-luck frontier resolver reaches them.
    """
    synced: dict[str, dict[str, Room]] = {}
    carriage_stops = {
        "rouvray": (
            "stop:rouvray-hollow-bells",
            "Hollow Bells Post",
            "deep_frontier",
        ),
    }
    for content in SECONDARY_REGIONS:
        _validate_authored_region(content)
        rows: dict[str, Room] = {}
        for room_key, data in content["rooms"].items():
            row = (await session.execute(
                select(Room).where(Room.content_id == data["id"])
            )).scalars().first()
            if row is None:
                row = Room(content_id=data["id"])
                session.add(row)
            row.name = data["name"]
            row.width = data["width"]
            row.height = data["height"]
            row.terrain = data["terrain"]
            row.objects = data.get("objects", [])
            row.spawn_points = data["spawn_points"]
            row.enemy_spawns = data.get("enemy_spawns", [])
            rows[room_key] = row
        await session.flush()

        # Rebuild only edges wholly owned by this authored region. External
        # edges are play-owned (frontier discoveries) or deliberately supplied
        # by another integration seam (the temporary Oakrun bridge), so they
        # must survive synchronization even when an internal door moves.
        region_room_ids = [row.id for row in rows.values()]
        await session.execute(delete(RoomConnection).where(
            RoomConnection.from_room_id.in_(region_room_ids),
            RoomConnection.to_room_id.in_(region_room_ids),
        ))
        for connection in content["connections"]:
            source = rows[connection["from"]]
            target = rows[connection["to"]]
            external_edge = (await session.execute(
                select(RoomConnection).where(
                    RoomConnection.from_room_id == source.id,
                    RoomConnection.from_x == connection["x"],
                    RoomConnection.from_y == connection["y"],
                )
            )).scalars().first()
            if external_edge is not None:
                raise ValueError(
                    f"Authored region {content['id']!r} cannot claim "
                    f"{connection['from']} ({connection['x']}, "
                    f"{connection['y']}): an external connection already "
                    "owns that door"
                )
            session.add(RoomConnection(
                from_room_id=source.id,
                to_room_id=target.id,
                from_x=connection["x"],
                from_y=connection["y"],
            ))

        if content["id"] in carriage_stops:
            stop_key, public_name, biome = carriage_stops[content["id"]]
            await ensure_carriage_stop(
                session,
                stop_key=stop_key,
                room_id=rows[content["start_room"]].id,
                biome=biome,
                world_minute=0,
                public_name=public_name,
                metadata={"authored": True, "region_id": content["id"]},
                status="closed",
            )
        synced[content["id"]] = rows
    return synced


async def _sync_drazna_carriage_topology(session) -> None:
    """Synchronize the Mudwheel and Grey Heron without revealing Drazna.

    The three physical Draznan stops and all routes touching them exist while
    closed so room interactions have a durable stop to inspect. Only an
    authored ``FrontierNode`` marks actual procedural discovery; the temporary
    Oakrun playtest bridge intentionally does not.
    """
    room_content_ids = (
        "oakrun_crossroads",
        "drazna_lantern_quays",
        "drazna_high_crown",
        "drazna_birch_heights",
    )
    rooms = {
        row.content_id: row
        for row in (await session.execute(
            select(Room).where(Room.content_id.in_(room_content_ids))
        )).scalars()
    }
    missing = set(room_content_ids) - set(rooms)
    if missing:
        raise ValueError(
            "Drazna carriage rooms are missing: " + ", ".join(sorted(missing))
        )

    discovered = (await session.execute(
        select(FrontierNode.id).where(
            FrontierNode.authored_region_id == "drazna"
        )
    )).scalars().first() is not None
    desired_stop_status = "operating" if discovered else "closed"

    stop_specs = (
        {
            "stop_key": "stop:drazna-lantern-quays",
            "room_content_id": "drazna_lantern_quays",
            "public_name": "Drazna Lantern Quays",
            "object_id": "drazna_quay_carriage",
            "accepts_generated_routes": True,
        },
        {
            "stop_key": "stop:drazna-high-crown",
            "room_content_id": "drazna_high_crown",
            "public_name": "Drazna High Crown",
            "object_id": "drazna_crown_mudwheel",
            "accepts_generated_routes": False,
        },
        {
            "stop_key": "stop:drazna-birch-heights",
            "room_content_id": "drazna_birch_heights",
            "public_name": "Drazna Birch Heights",
            "object_id": "drazna_heights_mudwheel",
            "accepts_generated_routes": False,
        },
    )
    stops: dict[str, CarriageStop] = {}
    for spec in stop_specs:
        room = rooms[spec["room_content_id"]]
        by_room = (await session.execute(
            select(CarriageStop).where(CarriageStop.room_id == room.id)
        )).scalars().first()
        by_key = (await session.execute(
            select(CarriageStop).where(
                CarriageStop.stop_key == spec["stop_key"]
            )
        )).scalars().first()
        if by_room is not None and by_key is not None and by_room.id != by_key.id:
            raise ValueError(
                f"Drazna carriage stop {spec['stop_key']!r} conflicts with "
                f"room {spec['room_content_id']!r}"
            )
        stop = by_room or by_key
        if stop is None:
            stop = CarriageStop(
                stop_key=spec["stop_key"],
                room_id=room.id,
                public_name=spec["public_name"],
                biome="drazna_marches",
                status=desired_stop_status,
                created_at_minute=0,
                details={},
            )
            session.add(stop)
        stop.stop_key = spec["stop_key"]
        stop.room_id = room.id
        stop.public_name = spec["public_name"]
        stop.biome = "drazna_marches"
        # A future damage system may own ``damaged`` after discovery. Content
        # sync still closes every stop when no procedural discovery exists.
        if not discovered or stop.status != "damaged":
            stop.status = desired_stop_status
        stop.details = {
            "authored": True,
            "region_id": "drazna",
            "service_id": "mudwheel",
            "activation_group": DRAZNA_CARRIAGE_ACTIVATION_GROUP,
            "physical_object_id": spec["object_id"],
            "accepts_generated_routes": spec["accepts_generated_routes"],
        }
        stops[spec["room_content_id"]] = stop
    await session.flush()

    oakrun_stop = (await session.execute(
        select(CarriageStop).where(
            CarriageStop.room_id == rooms["oakrun_crossroads"].id
        )
    )).scalars().first()
    if oakrun_stop is None:
        raise ValueError("Oakrun Exchange must exist before Drazna routes sync")
    oakrun_stop.details = {
        **(
            oakrun_stop.details
            if isinstance(oakrun_stop.details, dict)
            else {}
        ),
        "physical_object_id": "oakrun_covered_carriage",
    }

    mudwheel = _LIVING_WORLD_CONTENT.carriages["mudwheel"]
    climb = _LIVING_WORLD_CONTENT.routes["drazna-quay-high-crown"]
    birch = _LIVING_WORLD_CONTENT.routes["drazna-high-birch"]
    mudwheel_departures = [
        departure
        for departure in mudwheel["departures"]
        if departure["from_location_id"] == "drazna_lantern_quays"
    ]
    outbound_days = sorted({
        departure["day"] for departure in mudwheel_departures
    })
    outbound_minutes = sorted({
        int(departure["minute"]) for departure in mudwheel_departures
    })
    if not outbound_days or len(outbound_minutes) != 1:
        raise ValueError("Mudwheel requires one authored outbound clock time")
    outbound_minute = outbound_minutes[0]
    layover = int(mudwheel["layover_minutes"])
    crown_up_minute = outbound_minute + int(climb["travel_minutes"]) + layover
    birch_down_minute = crown_up_minute + int(birch["travel_minutes"]) + layover
    crown_down_minute = birch_down_minute + int(birch["travel_minutes"]) + layover

    grey_heron = _LIVING_WORLD_CONTENT.carriages["grey-heron"]
    grey_routes = [
        _LIVING_WORLD_CONTENT.routes[route_id]
        for route_id in grey_heron["route_ids"]
    ]
    grey_travel_minutes = sum(
        int(route["travel_minutes"]) for route in grey_routes
    ) + (
        max(0, len(grey_routes) - 1)
        * int(grey_heron["layover_minutes"])
    )
    grey_danger = sum(
        {"safe": 0, "guarded": 1, "dangerous": 2, "dire": 3}[route["risk"]]
        for route in grey_routes
    )

    route_specs = (
        {
            "route_key": "service:mudwheel:quays-to-crown",
            "from": stops["drazna_lantern_quays"],
            "to": stops["drazna_high_crown"],
            "travel_minutes": int(climb["travel_minutes"]),
            "fare": 2,
            "danger": 1,
            "activation_status": "operating",
            "departures": [outbound_minute],
            "details": {
                "service_id": "mudwheel",
                "passage_id": climb["id"],
                "risk": climb["risk"],
                "service_days": outbound_days,
                "boarding_grace_minutes": int(
                    mudwheel["service_rules"]["waits_minutes"]
                ),
                "capacity": int(mudwheel["capacity"]),
                "direction": "uphill",
            },
        },
        {
            "route_key": "service:mudwheel:crown-to-birch",
            "from": stops["drazna_high_crown"],
            "to": stops["drazna_birch_heights"],
            "travel_minutes": int(birch["travel_minutes"]),
            "fare": 1,
            "danger": 0,
            "activation_status": "operating",
            "departures": [crown_up_minute],
            "details": {
                "service_id": "mudwheel",
                "passage_id": birch["id"],
                "risk": birch["risk"],
                "service_days": outbound_days,
                "boarding_grace_minutes": int(
                    mudwheel["service_rules"]["waits_minutes"]
                ),
                "capacity": int(mudwheel["capacity"]),
                "direction": "uphill",
                "layover_before_minutes": layover,
            },
        },
        {
            "route_key": "service:mudwheel:birch-to-crown",
            "from": stops["drazna_birch_heights"],
            "to": stops["drazna_high_crown"],
            "travel_minutes": int(birch["travel_minutes"]),
            "fare": 1,
            "danger": 0,
            "activation_status": "operating",
            "departures": [birch_down_minute],
            "details": {
                "service_id": "mudwheel",
                "passage_id": birch["id"],
                "risk": birch["risk"],
                "service_days": outbound_days,
                "boarding_grace_minutes": int(
                    mudwheel["service_rules"]["waits_minutes"]
                ),
                "capacity": int(mudwheel["capacity"]),
                "direction": "downhill",
                "derived_return": True,
            },
        },
        {
            "route_key": "service:mudwheel:crown-to-quays",
            "from": stops["drazna_high_crown"],
            "to": stops["drazna_lantern_quays"],
            "travel_minutes": int(climb["travel_minutes"]),
            "fare": 2,
            "danger": 1,
            "activation_status": "operating",
            "departures": [crown_down_minute],
            "details": {
                "service_id": "mudwheel",
                "passage_id": climb["id"],
                "risk": climb["risk"],
                "service_days": outbound_days,
                "boarding_grace_minutes": int(
                    mudwheel["service_rules"]["waits_minutes"]
                ),
                "capacity": int(mudwheel["capacity"]),
                "direction": "downhill",
                "layover_before_minutes": layover,
                "derived_return": True,
            },
        },
    )

    grey_departures = {
        departure["from_location_id"]: departure
        for departure in grey_heron["departures"]
    }
    route_specs += (
        {
            "route_key": "service:grey-heron:oakrun-to-drazna",
            "from": oakrun_stop,
            "to": stops["drazna_lantern_quays"],
            "travel_minutes": grey_travel_minutes,
            "fare": int(grey_heron["fare_coin"]),
            "danger": grey_danger,
            "activation_status": "dangerous",
            "departures": [
                int(grey_departures["oakrun_pilgrims_hollow"]["minute"])
            ],
            "details": {
                "service_id": "grey-heron",
                "route_ids": list(grey_heron["route_ids"]),
                "risk": "dire",
                "service_days": [
                    grey_departures["oakrun_pilgrims_hollow"]["day"]
                ],
                "boarding_grace_minutes": int(
                    grey_heron["service_rules"]["waits_minutes"]
                ),
                "capacity": int(grey_heron["capacity"]),
                "layover_minutes": int(grey_heron["layover_minutes"]),
                "runtime_terminal_alias": "oakrun_pilgrims_hollow",
            },
        },
        {
            "route_key": "service:grey-heron:drazna-to-oakrun",
            "from": stops["drazna_lantern_quays"],
            "to": oakrun_stop,
            "travel_minutes": grey_travel_minutes,
            "fare": int(grey_heron["fare_coin"]),
            "danger": grey_danger,
            "activation_status": "dangerous",
            "departures": [
                int(grey_departures["drazna_lantern_quays"]["minute"])
            ],
            "details": {
                "service_id": "grey-heron",
                "route_ids": list(reversed(grey_heron["route_ids"])),
                "risk": "dire",
                "service_days": [
                    grey_departures["drazna_lantern_quays"]["day"]
                ],
                "boarding_grace_minutes": int(
                    grey_heron["service_rules"]["waits_minutes"]
                ),
                "capacity": int(grey_heron["capacity"]),
                "layover_minutes": int(grey_heron["layover_minutes"]),
                "runtime_terminal_alias": "oakrun_pilgrims_hollow",
            },
        },
    )

    for spec in route_specs:
        await _sync_authored_carriage_route(
            session,
            spec,
            activated=discovered,
        )


async def _sync_authored_carriage_route(
    session,
    spec: dict,
    *,
    activated: bool,
) -> CarriageRoute:
    """Upsert one definition-owned direction without duplicating old edges."""
    by_endpoints = (await session.execute(
        select(CarriageRoute).where(
            CarriageRoute.from_stop_id == spec["from"].id,
            CarriageRoute.to_stop_id == spec["to"].id,
        )
    )).scalars().first()
    by_key = (await session.execute(
        select(CarriageRoute).where(
            CarriageRoute.route_key == spec["route_key"]
        )
    )).scalars().first()
    if (
        by_endpoints is not None
        and by_key is not None
        and by_endpoints.id != by_key.id
    ):
        raise ValueError(
            f"Authored carriage route {spec['route_key']!r} conflicts with "
            "an existing directed edge"
        )
    route = by_endpoints or by_key
    if route is None:
        route = CarriageRoute(
            route_key=spec["route_key"],
            from_stop_id=spec["from"].id,
            to_stop_id=spec["to"].id,
            travel_minutes=spec["travel_minutes"],
        )
        session.add(route)
    route.route_key = spec["route_key"]
    route.from_stop_id = spec["from"].id
    route.to_stop_id = spec["to"].id
    route.travel_minutes = spec["travel_minutes"]
    route.fare = spec["fare"]
    route.danger = spec["danger"]
    route.status = spec["activation_status"] if activated else "closed"
    route.departures = list(spec["departures"])
    route.details = {
        "authored": True,
        "region_id": "drazna",
        "activation_group": DRAZNA_CARRIAGE_ACTIVATION_GROUP,
        "activation_status": spec["activation_status"],
        **spec["details"],
    }
    await session.flush()
    return route


async def _sync_temporary_oakrun_drazna_bridge(session) -> None:
    """Ensure the isolated test bridge without altering frontier discovery."""
    content_ids = {
        content_id
        for edge in TEMPORARY_OAKRUN_DRAZNA_BRIDGE
        for content_id in edge[:2]
    }
    rooms = {
        row.content_id: row
        for row in (await session.execute(
            select(Room).where(Room.content_id.in_(tuple(content_ids)))
        )).scalars()
    }
    missing = content_ids - set(rooms)
    if missing:
        if not TEMPORARY_OAKRUN_DRAZNA_BRIDGE_ENABLED:
            return
        raise ValueError(
            "Temporary Oakrun-Drazna bridge rooms are missing: "
            + ", ".join(sorted(missing))
        )
    for source_id, target_id, x, y in TEMPORARY_OAKRUN_DRAZNA_BRIDGE:
        source = rooms[source_id]
        target = rooms[target_id]
        if not TEMPORARY_OAKRUN_DRAZNA_BRIDGE_ENABLED:
            await session.execute(delete(RoomConnection).where(
                RoomConnection.from_room_id == source.id,
                RoomConnection.to_room_id == target.id,
                RoomConnection.from_x == x,
                RoomConnection.from_y == y,
            ))
            continue
        validate_connection(
            _ALL_AUTHORED_ROOMS[source_id],
            {"from_x": x, "from_y": y},
        )
        edge = (await session.execute(
            select(RoomConnection).where(
                RoomConnection.from_room_id == source.id,
                RoomConnection.from_x == x,
                RoomConnection.from_y == y,
            )
        )).scalars().first()
        if edge is None:
            session.add(RoomConnection(
                from_room_id=source.id,
                to_room_id=target.id,
                from_x=x,
                from_y=y,
            ))
        elif edge.to_room_id != target.id:
            raise ValueError(
                f"Temporary bridge door {source_id} ({x}, {y}) is already "
                "owned by another connection"
            )


async def _sync_authored_frontier_exits(session, rooms_by_key) -> None:
    """Seed frontier doors and restore any already-discovered connections."""
    for room_key, data in _OAKRUN_CONTENT["rooms"].items():
        source = rooms_by_key[room_key]
        for definition in data.get("frontier_exits", []):
            x, y = definition["x"], definition["y"]
            row = (await session.execute(
                select(FrontierExit).where(
                    FrontierExit.source_room_id == source.id,
                    FrontierExit.source_x == x,
                    FrontierExit.source_y == y,
                )
            )).scalars().first()
            if row is None:
                # Stable seed derived without Python's process-randomized hash.
                seed_material = f"{data['id']}:{x}:{y}".encode("utf-8")
                import hashlib
                roll_seed = int.from_bytes(
                    hashlib.blake2b(seed_material, digest_size=4).digest(),
                    "big",
                )
                row = FrontierExit(
                    source_room_id=source.id,
                    source_x=x,
                    source_y=y,
                    status="frontier",
                    roll_seed=roll_seed,
                    biome_hint=definition.get("biome_hint"),
                    generator_hint={"label": definition.get("label", "Uncharted road")},
                    created_at_minute=0,
                )
                session.add(row)
            elif row.status == "connected" and row.target_room_id is not None:
                session.add(RoomConnection(
                    from_room_id=source.id,
                    to_room_id=row.target_room_id,
                    from_x=x,
                    from_y=y,
                ))
