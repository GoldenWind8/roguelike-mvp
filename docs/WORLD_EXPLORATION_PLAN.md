# World Exploration Plan

This is the practical bridge between the current combat prototype and the next
coding milestone. It intentionally avoids Redis, multi-worker routing, full
auth, and autonomous NPC simulation.

For the larger architecture that may come after this simple loop works, see
[World Architecture Proposal](WORLD.md).

## Goal

Let a player explore connected rooms in the browser:

```mermaid
sequenceDiagram
    participant Player
    participant Client
    participant Server
    participant DB

    Player->>Client: move onto door tile
    Client->>Server: movement intent
    Server->>Server: validate movement
    Server->>DB: find room connection
    DB-->>Server: destination room id
    Server->>DB: load destination room
    Server->>Server: build new room state
    Server-->>Client: state update
    Client-->>Player: render new room
```

## Design Constraints

- Server remains authoritative.
- One process is fine.
- One active room at a time is acceptable for the first traversal version.
- Do not rewrite combat.
- Do not build a distributed room service yet.
- Keep AI out of the first traversal path; hand-authored room data is enough.

## Current Foundation

Already available:

- `Room`, `RoomConnection`, and `EnemyDef` models.
- Seeded default room and antechamber.
- Door tiles in terrain.
- Validation for door/portal connection origins.
- `load_level(session, room_id)` for turning a DB room into runtime data.
- `Game(level)` for running combat from `LevelData`.

Missing:

- Runtime awareness of current `room_id`.
- Destination arrival coordinates.
- Frontend support for variable room dimensions.
- Client rendering for objects and room metadata.
- An exploration movement path that does not wait for a combat round.

## Proposed Implementation Order

### 1. Make Room Dimensions Part Of State

Add `width`, `height`, and probably `room_id` / `room_name` to the state sent to
the client.

Then update the frontend grid creation:

- Use `gameState.width` and `gameState.height`.
- Set CSS grid columns dynamically.
- Remove hardcoded `10`.
- Guard all grid reads by actual dimensions.

This should be the first code change because it makes every later room feature
visible.

### 2. Represent Room Mode

Add a room mode concept before adding many actions:

```text
exploration: immediate movement and interaction
combat: turn-based action submission
```

At first, the mode can come from room metadata or a simple default. If metadata
is not in the schema yet, start with a conservative server-side rule and promote
it to persisted data once the behavior is understood.

### 3. Add A Minimal Room Runtime Seam

Keep the shape small:

```mermaid
flowchart LR
    A["Room row"] --> B["load_level"]
    B --> C["LevelData"]
    C --> D["Game / Room runtime"]
    D --> E["state_update"]
```

Possible first step:

- Store the current `room_id` next to `Game`.
- When traversal happens, load a new `LevelData`.
- Replace or rebuild the runtime for that destination room.

This is not the final MMO shape, but it proves traversal without inventing a
large room orchestration layer too early.

### 4. Support Door Traversal

Add server logic:

- Detect when a player enters a door/portal tile.
- Query `RoomConnection` by `from_room_id`, `from_x`, and `from_y`.
- Load the destination room.
- Place the player at a destination spawn.
- Broadcast the new state.

The current schema only stores the origin tile. Soon after traversal works, add:

- `to_x`
- `to_y`
- possibly `kind` such as `door`, `portal`, or `path`

Until then, using the first destination spawn is acceptable.

### 5. Add Exploration Movement

Exploration movement should not wait for every player to submit a turn.

Keep the same validation instincts:

- Is the player alive/present?
- Is the target in bounds?
- Is the tile passable?
- Is the tile occupied?
- Does the tile trigger traversal?

The difference is timing: apply movement immediately in exploration rooms.

### 6. Send Objects To The Client

The DB already stores room objects, but `LevelData` currently does not expose
them to the runtime/client.

First object payload can be simple:

```json
{
  "id": "object_1",
  "type": "chest",
  "position": [1, 1],
  "label": "Chest"
}
```

Do not build full object state yet. First prove that objects can be seen and
selected.

### 7. Add Examine Object

Add a server-owned interaction:

```mermaid
sequenceDiagram
    participant Client
    participant Server
    Client->>Server: examine object_id
    Server->>Server: validate object exists in current room
    Server-->>Client: object description / result
```

First results can be hand-authored:

- "The chest is old and iron-bound."
- "The fire barrel smells of oil."
- "You find a bomb." only if you are ready for minimal item state.

### 8. Add Basic NPC Dialogue

Do this after object examination. Dialogue adds UI and server state, so it is
easier once one room interaction path already exists.

First NPC shape:

- `id`
- `name`
- `position`
- `description`
- `personality`
- `dialogue_context`

Dialogue state should be scoped to a player and NPC. The NPC response may come
from a model, but any gameplay effects must still become validated data before
they affect the world.

## What Not To Build Yet

- Multiple active room workers.
- Redis pub/sub.
- Reconnect routing.
- Account auth.
- Persistent inventory.
- Procedural generation during traversal.
- NPC autonomous schedules.
- Complex traps or hazards.

## First Exploration Definition Of Done

The first satisfying milestone is:

- Player starts in the pillared hall.
- Frontend renders the room using backend dimensions.
- Player can move to a door.
- Server loads the antechamber.
- Frontend renders the antechamber correctly.
- Player can move back.
- Existing combat still works.

That is the point where the project stops being only a combat prototype and
becomes the beginning of an explorable world.
