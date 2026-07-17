# Current Architecture

This document is the single source of truth for how the project works today —
runtime, backend boundaries, and persistence. For gameplay direction see
[Game Design](GAME_DESIGN.md). For near-term build order see
[Roadmap](ROADMAP.md). For deferred architecture ideas see
[Future Ideas](FUTURE.md).

## Current Runtime

The app is currently one process:

- FastAPI serves the static frontend.
- FastAPI owns one WebSocket endpoint at `/ws`.
- `active_rooms` is a registry of live `RoomRuntime`s. Each runtime owns one
  room's `RoomEngine`/`RoomState`, the WebSockets of the players inside it, and
  that room's round timer. `player_room` maps each player to their room.
- Rooms load lazily: the first player to enter a room triggers `load_room`
  (template) plus `load_npcs` (individuals); when the last player leaves, the
  runtime is evicted. Eviction is "save individuals, unload" (NPCS.md
  Decision 10): NPC rows are written back, fungible enemy state is
  deliberately forgotten — the next visit reseeds enemies from the template
  and reloads NPCs from their rows.
- One global `asyncio.Lock` still serializes all live state mutation. Helpers
  never acquire it themselves (asyncio locks are not reentrant) — only the
  top-level WebSocket handlers and the round-timeout task do.
- SQLAlchemy loads room definitions from the local SQLite database.

```mermaid
flowchart TB
    B["React browser client"] <-- "HTTP: Vite production assets" --> A["FastAPI app"]
    B <-- "WebSocket: /ws" --> A
    A --> REG["active_rooms registry"]
    REG --> R1["RoomRuntime: room A"]
    REG --> R2["RoomRuntime: room B"]
    R1 --> G1["RoomEngine / RoomState"]
    R2 --> G2["RoomEngine / RoomState"]
    A --> L["room_loader.load_room"]
    L --> DB["SQLite via SQLAlchemy"]
    DB --> R["Room / EnemyDef / RoomConnection"]
```

This is a good shape for the current prototype. It is not production MMO
infrastructure, and it does not need to be yet.

## Current Startup Flow

At startup, the server:

1. Creates database tables if they do not exist.
2. Seeds the default rooms if the database is empty (and backfills seed NPCs
   into a database created before the `npcs` table existed).
3. Remembers the default room id.
4. Accepts WebSocket joins.

On shutdown it saves the individuals of every still-live room — NPCs and,
since M8, connected players' rows — because rooms with players connected
never went through eviction, and a restart must not be the one remaining way
to destroy an individual's state.

No `RoomEngine` is built at startup — the first join loads the default room through
the same `get_or_load_room` path traversal uses.

```mermaid
sequenceDiagram
    participant App as FastAPI lifespan
    participant DB as Database
    participant Loader as Room loader
    participant Reg as active_rooms

    App->>DB: init_db()
    App->>DB: get_or_seed_default_room()
    Note over App: first join or traversal
    App->>Reg: get_or_load_room(room_id)
    Reg->>Loader: load_room(room_id)
    Loader->>DB: read room, enemy defs, connections
    Loader-->>Reg: RoomTemplate -> RoomRuntime(RoomEngine)
```

## Dependency Layers

The combat engine has a useful one-way dependency shape:

```text
config / entities / actions / events
              |
              v
room_state.py RoomState, source of truth
              |
              v
effects.py    atomic mutations
              |
              v
handlers.py   one handler per action type
              |
              v
systems.py    round resolution and enemy phase
              |
              v
modes.py      RoomMode: WHEN actions resolve (buffered vs immediate)
              |
              v
room_engine.py round lifecycle
              |
              v
main.py       WebSocket and FastAPI boundary
```

Rule of thumb: if a lower layer needs something from a higher layer, move the
shared concept down instead of creating an import cycle.

Three modules sit beside this stack at the `main.py` edge, not inside it:
`npc_store.py` (rows ↔ NPC entities, the individual-persistence twin of
`room_loader.py`), `persona.py` (the validation gate for persona documents),
and `dialogue.py` (the `DialogueProvider` seam). The engine never imports
them — an NPC inside `RoomState` is just an `Actor`.

In memory there is one actor shape (`entities.Actor`; NPCS.md Decision 4):
`Player`, `Enemy`, and `NPC` are thin dataclass subclasses, and combat,
bombs, and occupancy target `Actor` without caring which one they hit.
Disposition (`hostile | neutral | friendly`) is a field, not a class — a
shopkeeper turning hostile is a field write.

The three kinds differ on two orthogonal axes, never on "kind of thing":

- **Behavior comes from data, not type.** `brains.select_brain(actor)` picks a
  brain each round from the actor's disposition + party membership: a hostile
  actor chases, a follower defends, everyone else idles. A `Player` has no
  brain (its socket drives it). So a hostile `Enemy` and an `NPC` soured to
  hostile behave *identically*.
- **Persistence is the real `Enemy`/`NPC` split.** An `Enemy` is a *simple*,
  fungible actor — a hostile NPC minus the row: its hp/position are forgotten
  on reset and it respawns from the template. An `NPC` is an individual whose
  state is worth an `npcs` row. Recruiting an enemy promotes fungible → individual.

## Room Modes

Every room runs one of two timing models (the `RoomMode` seam, implemented in
`backend/modes.py`):

| Mode | Resolution | Allowed actions |
|---|---|---|
| `combat` (`TurnBasedMode`) | buffer actions, resolve when all players submit or the timeout fires | move, attack, bomb, wait |
| `exploration` (`ExplorationMode`) | validate and resolve immediately, per action | move |

The mode decides *when* an action resolves, never *how*: both modes validate
and resolve through the same `HANDLERS`, so movement rules, door traversal,
effects, and events are one rules engine. Exploration rooms have no rounds,
no round timers, no `waiting_for` broadcasts, and no enemy phase.

Talking to an NPC is not an action in either mode — like `inspect_object`,
it is a request outside the action economy (NPCS.md Decision 1): it never
consumes a turn and never pauses the round timer, so it works mid-combat and
cannot be used to stall a round.

A room's mode is a **live, derived property** (M7 escalation), never stored:
`modes.derive_mode` answers `combat` iff a living actor hostile to the players
is present, `exploration` otherwise. `RoomEngine.refresh_mode` re-evaluates it
at every resolution point — after a round resolves and after dialogue effects
land — and when the answer changes it swaps timing models in place, clears any
half-collected round on de-escalation, and emits `room_mode_changed` (the
round timer is cancelled in `main`). So an insulted caretaker turns his room
into a fight, and clearing the last hostile — by blade or by parley — makes
the room explorable without a reload. The live mode is sent to the client in
`state.room.mode`; there is no `mode` field on templates or rows to go stale.

## Combat Model

Combat is server-authoritative. The client sends intent; the server validates,
resolves, mutates state, and broadcasts the result.

```mermaid
flowchart LR
    C["Client action"] --> M["main.py"]
    M --> G["RoomEngine.submit_action"]
    G --> V["validate_player_action"]
    V --> P["RoomState.pending_actions"]
    P --> R{"All players acted or timeout?"}
    R -- "no" --> W["waiting_for"]
    R -- "yes" --> S["systems.resolve_round"]
    S --> E["events"]
    E --> B["broadcast state_update"]
```

The round order is:

1. Player actions are collected.
2. Movement actions resolve first.
3. Attack/bomb/wait actions resolve next.
4. Actor phase resolves (every non-player actor with a brain — hostiles chase,
   followers defend; the brain is chosen from data by `brains.select_brain`).
5. The round increments.
6. A full state update and event list are sent to clients.

There is no win/lose step: this is an infinite, procedurally-generated world,
not a match. Clearing a room isn't victory (it just means no hostiles remain —
M7 reads that to flip the room to exploration), and a party wipe isn't a
terminal defeat (death/respawn is its own future concern).

## Actions, Handlers, Effects, Events

This is the core extension pattern:

```mermaid
flowchart LR
    A["Action: player intent"] --> H["Handler: validate and resolve"]
    H --> F["Effect: atomic mutation"]
    F --> AP["apply_effect"]
    AP --> EV["Event: client-readable fact"]
```

| Concept | Role | Example |
|---|---|---|
| Action | A player's requested intent | move north, attack enemy, throw bomb |
| Handler | Validates and resolves one action type | `AttackHandler`, `BombHandler` |
| Effect | Atomic state mutation | `Damage(target, amount)` |
| Event | Record of what happened | `player_attacked`, `enemy_died` |

Adding a combat action should usually mean:

1. Add an `ActionType`.
2. Extend action parsing.
3. Add a handler.
4. Register the handler.
5. Reuse existing effects where possible.

Do not widen `RoomState` or `resolve_round` just because a new action is
flavorful. Push variety to handlers and effect data.

## Traversal Model

Traversal splits cleanly between the pure engine and the async edge:

1. A MOVE resolves onto a door/portal tile that has a `room_connections`
   entry (`RoomTemplate.connections`, loaded with the room).
2. `MoveHandler` appends a `PLAYER_ENTERED_DOOR` event — the engine stays
   sync and DB-free; it only announces intent.
3. After the round resolves, `handle_round_events` in `main.py` reacts:
   validate the destination fully (load it if dormant, check capacity and a
   free spawn), and only then mutate — detach the player from the old
   runtime, move their socket, attach them at the destination spawn.
4. The traveler gets a `room_changed` message; the old room's remaining
   players see them slip away in the normal `state_update`.
5. A denied traversal (room full, no free spawn, load failure) changes
   nothing: the player stays on the door tile and gets an error.

```mermaid
sequenceDiagram
    participant P as Player
    participant Old as Old RoomRuntime
    participant Edge as handle_round_events
    participant New as Destination RoomRuntime

    P->>Old: move onto door tile
    Old->>Old: resolve round, emit PLAYER_ENTERED_DOOR
    Old->>Edge: round events
    Edge->>New: get_or_load_room + capacity/spawn checks
    Edge->>Old: detach player + socket
    Edge->>New: attach player at free spawn
    New-->>P: room_changed + full state
    Old-->>Old: broadcast state (traveler gone), evict if empty
```

This runs at all three round-resolution sites: action submission, round
timeout, and the auto-resolve that fires when a pending player disconnects.

## WebSocket Message Shape

```mermaid
sequenceDiagram
    participant Client
    participant Server
    participant RoomEngine

    Client->>Server: POST /register or /login (HTTP)
    Server-->>Client: signed session token
    Client->>Server: join with token (first WS message)
    Note over Server: token -> players row (M8), reject if invalid or already connected
    Server->>RoomEngine: attach_player(player, saved position)
    RoomEngine-->>Server: events
    Server-->>Client: join_ack
    Server-->>Client: state_update

    Client->>Server: action
    Server->>RoomEngine: submit_action(player_id, data)
    RoomEngine-->>Server: events and resolved
    Server-->>Client: action_locked or error
    Server-->>Client: state_update when round resolves
    Note over Server,Client: if the move entered a connected door
    Server-->>Client: room_changed with the destination room's full state
```

Broadcasts are room-scoped: a `state_update` only reaches the players in that
room. A traversing player receives `room_changed` instead of the old room's
final `state_update`. Two request/reply pairs bypass the action pipeline
entirely: `inspect_object` → `object_inspection` and `talk` → `npc_dialogue`
(see "NPC Dialogue Flow").

The client should remain a renderer and input collector. The server decides
what is legal.

## NPC Dialogue Flow (M4)

A `talk` message is validated under the room lock (NPC exists, alive,
adjacent — the attack targeting rule), but the dialogue provider runs
OUTSIDE the lock: never generate dialogue while holding the room lock.
Rounds keep resolving during a slow LLM call; when the response arrives the
handler re-acquires the lock and re-validates (the room may have evicted,
the NPC may have died) before appending to the NPC's bounded transcript and
replying — dialogue is one-on-one, so `npc_dialogue` goes only to the
talking player.

```mermaid
sequenceDiagram
    participant C as Client
    participant M as main.handle_talk
    participant P as DialogueProvider

    C->>M: talk {npc_id, text}
    Note over M: under state_lock: validate npc, adjacency
    M->>P: reply(npc, player, text)  [lock released]
    Note over P: GridProvider: LLM call, hard timeout;<br/>any failure -> CannedProvider line
    P-->>M: response text
    Note over M: re-acquire lock, RE-validate,<br/>append bounded transcript
    M-->>C: npc_dialogue (talker only)
```

The provider seam (`dialogue.py`) has both implementations live:
`GridProvider` (AI Power Grid chat completions; key/model from env) wrapping
`CannedProvider` (deterministic persona lines) as its fallback — timeout,
rate limit, or provider-down all degrade to a canned line and the request is
dropped, never queued. Player text reaches the prompt only inside a
delimited untrusted block; M4 is text-only, so nothing an LLM says can
mutate game state. The effect channel (closed vocabulary, engine-validated)
is the next slice — see NPCS.md "Dialogue: Two Channels".

## Validation Pattern

Validation happens twice:

- Submission-time validation gives quick feedback.
- Resolution-time validation is authoritative.

This matters because the world can change between submission and resolution. A
target may move, die, or become invalid before the action resolves.

## Data Loading Boundary

Room definitions live in the database. Live combat state lives in memory.

```mermaid
flowchart LR
    R["Room row"] --> LD["load_room"]
    ED["EnemyDef rows"] --> LD
    LD --> L["RoomTemplate"]
    L --> W["RoomState"]
    W --> S["state_update"]
```

The database provides template data:

- Room id and name.
- Room width and height.
- Terrain.
- Spawn points.
- Object definitions.
- Enemy placements.
- Room connections.

`RoomState` owns live runtime state:

- Player positions and HP.
- Enemy positions and HP.
- NPC entities (loaded from `npcs` rows — the second occupant source).
- Occupancy grid.
- Client-safe object summaries.
- Pending actions.
- Current round.

Do not write live mutations back into the room template. A chest being opened or
an enemy dying is session state, not a change to the authored room definition.
NPCs are the exception that proves the rule: their live state IS durable, but
it writes back to their own instance rows, never to the template.

Keep this boundary bright:

| Kind | Examples | Lives In |
|---|---|---|
| Template data | terrain, spawn points, enemy definitions, room objects | database |
| Live fungible state | player HP, enemy HP, positions, pending actions, current round | memory |
| Individual state | NPC hp/position/disposition/persona/transcript | memory while loaded, `npcs` rows at rest (saved on eviction/shutdown) |
| Future durable player data | account, inventory, current room, progression | database later |

## Persistence Strategy

The database (SQLite through SQLAlchemy async sessions) is touched only at
the edges — room load, room eviction, shutdown — never during rounds. It
stores room template data (`rooms`, `room_connections`, `enemy_defs`) and
NPC instance rows (`npcs`) — see [Database Schema](DB_SCHEMA.md). This is
real persistence for rooms and NPCs, not yet for players.

SQLite is good enough while the project is one process and local development is
fluid. SQLAlchemy is the useful abstraction: tests get lightweight databases,
model definitions stay in one place, and a later move to Postgres means less
rewriting. Postgres earns its place when there is real durable player/generated
data, more than one process, or production deployment pressure.

Migration policy while local data is disposable: recreate the SQLite database
from models, keep seed data idempotent, let tests prove the schema and loader.
Once real data matters, add Alembic and stop dropping tables.

## Current Limitations

These are accepted constraints for the current prototype:

- Room mode is derived from disposition live (M7): a soured NPC escalates its
  room to combat, and the last hostile falling de-escalates it. There is no
  authored `mode` override — a room that must *stay* peaceful (or hostile)
  regardless of occupants would need a new column feeding the predicate.
- Exploration rooms only accept movement as an *action* — examining
  (`inspect_object`) and talking (`talk`) are requests outside the action
  economy.
- Dialogue has two channels (M5+M6): text never mutates state, but validated
  effect proposals from the closed vocabulary can (`set_disposition`,
  `join_party`/`leave_party`). NPCs now act via brains chosen from their data —
  hostiles chase, followers defend — but only inside a combat room's actor phase.
- Evicted rooms reset their fungible state — enemies respawn from the seed
  on the next visit; NPCs persist as individuals.
- One global lock serializes all rooms (per-room locks are a later
  optimization; the single lock also guarantees rooms are never double-loaded).
- An enemy standing on a door tile blocks traversal until it moves.
- Objects can be inspected for text, but object effects and inventory are not
  implemented yet.
- Disconnect removes the player.
- Restart drops the live game state.
- There is no player account or persistent inventory.

These are not failures. They are the correct next set of engineering choices to
make as the world grows beyond hand-seeded rooms.

## Next Architectural Move

The next architectural move should be small:

```text
room registry + traversal (done)
        |
        v
exploration movement timing (RoomMode seam, done)
        |
        v
basic NPC dialogue + individual persistence (Milestone 4, done)
        |
        v
dialogue effects: closed vocabulary, set_disposition first (Milestone 5, done)
        |
        v
party members + escalation (Milestones 6-7, done)
        |
        v
identity + accounts: players table, reconnect claims your row (Milestone 8, done)
        |
        v
client migration + UI design (Milestones 9-10)
        |
        v
room generation joins from its parallel track (preset registry -> traversal
wiring -> AI config-picker)
        |
        v
later, only if needed: per-room locks, workers, Redis
```

Avoid jumping straight to workers, Redis, or gateway routing. Identity (M8)
is done — the `players` table, follower rebinding, and a stable owner for
future inventory all exist; the next unlock is the client (migration, then
UI). Room generation continues on a parallel track and is wired into
traversal once its presets are workable. See [Roadmap](ROADMAP.md) and
[Accounts & Identity](archive/ACCOUNTS.md).
