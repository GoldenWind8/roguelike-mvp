# Database Schema

This document describes the current SQLAlchemy data model and the likely next
tables. It is about durable data shape, not live runtime state.

Related docs:

- [Current Architecture](ARCHITECTURE.md): how the backend uses the database
  today, including the template-data vs. live-state boundary.
- [Future Ideas](FUTURE.md): Postgres, Redis, workers, and scale-out.

## Current Schema

The current database stores room templates, directed room connections,
reusable enemy definitions, and NPC instance rows (the first individual
state — see NPCS.md Decisions 9–10).

```mermaid
erDiagram
    ROOMS ||--o{ ROOM_CONNECTIONS : "from_room_id"
    ROOMS ||--o{ ROOM_CONNECTIONS : "to_room_id"
    ENEMY_DEFS }o..o{ ROOMS : "soft ref via enemy_spawns"
    NPCS }o--|| ROOMS : "room_id (location, not ownership)"

    NPCS {
        int id PK
        int room_id FK "where it is, not room design data"
        string name
        int x
        int y
        int hp
        int max_hp
        int defense
        int attack_damage
        bool is_alive
        string disposition "hostile | neutral | friendly"
        json persona "validated against the persona schema"
        json memory "bounded dialogue transcript"
    }

    ROOMS {
        int id PK
        string name
        int width
        int height
        json terrain "ASCII grid rows"
        json objects "room object definitions"
        json spawn_points "player arrival positions"
        json enemy_spawns "enemy id and position"
    }

    ROOM_CONNECTIONS {
        int id PK
        int from_room_id FK
        int to_room_id FK
        int from_x "origin door or portal x"
        int from_y "origin door or portal y"
    }

    ENEMY_DEFS {
        int id PK
        string name
        int hp
        int attack_damage
        int defense
        json on_spawn "future effect list"
        json on_death "future effect list"
    }
```

## Current Tables

| Table | Current role | Notes |
|---|---|---|
| `rooms` | Room template data | Terrain, objects, spawns, and enemy placements. This is not live session state. |
| `room_connections` | Directed world graph edges | Traversal uses these: `load_room` reads a room's outgoing edges into `RoomTemplate.connections`, and stepping onto a connected tile transfers the player. Arrival uses the destination's first free spawn (`to_x`/`to_y` are still future columns). |
| `enemy_defs` | Reusable enemy catalog | Rooms reference enemy ids from JSON and load stats from this table. Planned: runtime-appendable by LLM world generation, gated by schema/bounds validation (stat ranges; effect lists drawn from the closed vocabulary). |
| `npcs` | NPC instance rows (individuals) | One row per NPC that exists in the world; play edits it (hp, position, disposition, memory) and it survives room resets and restarts. Loaded/saved by `npc_store.py`; eviction is "save individuals, unload". Full stats are columns because an individual's wounds and buffs are instance state. `persona` is validated by `persona.validate_persona` on insert *and* load. `party_owner_id` arrives with the party-effects slice. |

## Important Boundary

Durable data now splits three ways, per NPCS.md's "a row exists to
remember something":

- **Template data** (`rooms`, `room_connections`, `enemy_defs`): authored or
  generated definitions. Never edited because something happened during
  play. Generation (human or LLM) may *append* — a new room, a new enemy
  def — through validation, but play never mutates.
- **Individual state** (`npcs` — current; `players` — planned): instance rows
  that *are* edited by play (hp, position, room, party membership) and survive
  room resets and restarts.
- **Ephemeral state** (fungible enemy hp/positions, round counters): lives
  only in memory and is deliberately forgotten on room reset — enemy
  respawn is a feature, not a persistence gap.

Examples:

- Fungible enemy died: ephemeral — respawns on next room load.
- Player moved or took damage: individual state — persists.
- NPC joined a party: individual state — persists.
- Chest opened: ephemeral today; becomes individual state when
  `object_instances` exists.
- Room terrain generated and accepted: template data.
- LLM invents a new enemy type: template data (append to `enemy_defs`
  through the validation gate).

This boundary prevents one player's session from corrupting canonical
definitions, while letting individuals accumulate history.

## JSON Columns

The schema uses JSON for variable-shape content:

- `rooms.terrain`
- `rooms.objects`
- `rooms.spawn_points`
- `rooms.enemy_spawns`
- `enemy_defs.on_spawn`
- `enemy_defs.on_death`

The database cannot fully enforce the shape inside these JSON blobs. Validation
therefore happens in app code before insert/load.

Current validation checks include:

- Required fields.
- Terrain dimensions.
- Valid tile characters.
- Walkable placements.
- No overlapping spawns/enemies/objects.
- Enemy references point to known enemy definitions.
- Spawns are near an entry.
- Required objects and entries are reachable.

This is the right trade for now: room data stays easy for humans and future AI
to generate, while the server still rejects malformed content.

## Soft References

`rooms.enemy_spawns[].enemy_id` is a soft reference because it lives inside JSON.
The database does not enforce it as a foreign key.

The app validates it with known enemy ids before storing seed room data, and
`load_room` fails if a room references an unknown enemy.

If this becomes painful, promote enemy spawns to a real join table:

```text
room_enemy_spawns(room_id, enemy_def_id, x, y)
```

Do that only when integrity and queryability matter more than keeping room data
as a compact blob.

## Near-Term Schema Additions

These are likely to matter for exploration.

```mermaid
erDiagram
    NPCS }o--|| ROOMS : "room_id (location, not ownership)"
    ROOMS ||--o{ ROOM_CONNECTIONS : "from/to"
    PLAYERS }o--|| ROOMS : "current_room_id"
    NPCS }o--o| PLAYERS : "party_owner_id"
    PLAYERS ||--o{ PLAYER_INVENTORY : "player_id"
    ITEMS ||--o{ PLAYER_INVENTORY : "item_id"

    NPCS {
        int id PK
        int room_id FK "where it is, not who owns it"
        string name
        int x
        int y
        int hp
        string disposition
        json persona "schema-validated document"
        int party_owner_id FK "null unless recruited"
    }

    PLAYERS {
        int id PK
        string name
        int current_room_id FK
        int x
        int y
        int hp
        datetime created_at
    }

    ITEMS {
        int id PK
        string name
        json effects "closed vocabulary"
        string source "hand or ai"
        json provenance
        datetime created_at
    }

    PLAYER_INVENTORY {
        int id PK
        int player_id FK
        int item_id FK
        int quantity
    }
```

| Addition | Why it may be needed |
|---|---|
| `room_connections.to_x`, `to_y` | Place players at a specific destination tile after traversal. |
| `room_connections.kind` | Distinguish door, portal, path, stairs, etc. |
| `npcs` | **Done** (see Current Tables) except `party_owner_id`, which lands with the party-effects slice because it needs an owner to point at. |
| `players` | Individual player state that survives disconnect and restart; also gives inventory/progression a stable owner. Open question: identity — how a returning connection claims its row. |
| `items` | Store generated or hand-authored item definitions. |
| `player_inventory` | Let items follow players between rooms. |

Do not add all of these at once. Add the smallest table/column needed by the
next milestone.

## Future Tables

These belong later:

| Table | Purpose |
|---|---|
| `events` | Append-only room/player event log for replay and debugging. |
| `room_sessions` | Distinguish live mutable session state from room templates. |
| `object_instances` | Track opened chests, destroyed barrels, dropped items, etc. |
| `npc_memory` | Store durable NPC/player relationship summaries. |

## SQLite Now, Postgres Later

The current app uses SQLAlchemy, so the model layer can survive a later move
from SQLite to Postgres.

Stay on SQLite while:

- Data is disposable.
- The app is one process.
- The goal is fast local iteration.

Move to Postgres when:

- Real players or generated content must be preserved.
- More than one process writes data.
- Production deployment needs stronger concurrency guarantees.

## Keep This Doc Current

When `backend/models.py` changes, update this file in the same change.

For each schema change, record:

- What changed.
- Whether it is current or planned.
- Whether the data is template, live/session, or player-owned.
