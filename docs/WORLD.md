# Architecture — Part 3: Open World, Rooms & Client

How the combat engine (Part 1) and the persistence plan (Part 2) grow into the
two-mode shared world of [`VISION.md`](VISION.md): exploration rooms, combat
rooms, AI-generated content, and the client that renders it all.

> **Status: proposal (2026-07), not yet built.** Written to be edited — every
> section is a decision you can push back on before implementation starts.
> This supersedes the June MVP scoping that deferred exploration and NPCs.

---

## Principles

1. **One pipeline, two schedules.** Exploration is not a second engine. Both
   modes run the same Actions → Effects → Events pipeline (Part 1); the *only*
   difference is **when** actions resolve — batched per round, or immediately.
2. **The room is the unit of everything.** Ownership, locking, generation,
   dormancy, persistence, and (later) horizontal scaling all happen per-room.
3. **AI proposes at the edges; the engine disposes in the middle.** Generation
   returns validated data (rooms, NPC definitions, item effects) — never live
   code, never direct state mutation. NPC *behavior* goes through the same
   action validation as player input.
4. **Don't simulate what no one is watching.** No global heartbeat. Work is
   scheduled lazily, and a room with no players in it goes fully dormant.
5. **Text-first client.** This game is text, dialogue, lore, and a grid — not
   animation. The client is DOM + CSS on a Vite/TypeScript scaffold; a canvas
   renderer was considered and rejected (see Client section).

---

## Rooms and modes

Today `Game` (game.py) is one hardcoded combat room. It becomes `Room`:

```
Room
├── WorldState            shared state: grid, entities, objects   (unchanged)
├── handlers / effects    shared rules: the Part-1 pipeline       (unchanged)
└── mode: RoomMode        the ONLY thing that differs between play styles
```

The `RoomMode` seam, precisely — because this is the highest-risk abstraction
in the plan and it should be agreed on paper before it's built:

```python
class RoomMode(ABC):
    @abstractmethod
    def submit(self, room: Room, player_id: str, action: Action) -> list[GameEvent]:
        """Player intent arrives over the socket.
        TurnBased: buffer it, resolve the round when everyone's in.
        Realtime: validate and resolve it right now."""
```

That's the whole interface. Anything time-driven (round timeout, NPC turns) is
the mode's internal business, done with `room.schedule` (see 'Timed work').

| Mode | Resolution | Pacing | Timed work |
|---|---|---|---|
| `TurnBasedMode` | buffer → `resolve_round` when all submitted | round-based | the 30s round timeout — today's *global* timer task, made per-room |
| `RealtimeMode` | validate → resolve → broadcast, per action | server-enforced per-player cooldown (~150–200ms) so the server, never the client, decides movement speed | NPC decisions, world events |

`TurnBasedMode` is today's `Game` logic *moved, not rewritten* — behavior
identical, combat rooms keep working throughout.

> ⚠️ **Leaky-abstraction tripwire:** if `RealtimeMode` ever needs a special
> case *inside* `handlers.py` or `effects.py`, the seam is wrong. That's a
> stop-and-redesign signal during milestone A's design review — not a surprise
> to discover in milestone D.

**Combat igniting within exploration** (VISION.md): for the MVP, a combat
encounter **is a room** — you walk through a door into it, and back out when
it's done. The world graph already models this. In-place mode switching (a
fight erupting inside an exploration room) is *enabled* by this design — it's
"swap the mode object" — but deliberately deferred until the two modes are
stable on their own.

---

## Multi-room runtime (single process, worker-shaped)

Part 2's target topology (gateway + room workers + Redis) is the scale-out
path. What we build now is **that topology's internals folded into one
process** — so a `Room` lifts out into a worker later without changing:

- **`RoomManager`** — `dict[room_id, Room]`, `get_or_load(room_id)` builds a
  `Room` from its Postgres template on first entry.
- **One `asyncio.Lock` per room** — the Part-1 global lock dies. Rooms mutate
  independently; a slow combat round in room A never stalls room B.
- **Connection routing** — the socket layer maps `player_id → room_id` and
  forwards each message to the player's current room. (This dict is exactly
  what Redis routing/presence replaces in the multi-process future.)
- **Dormancy** — when the last player leaves a room: cancel its pending
  timed tasks and evict it from memory. Durable outcomes were already persisted;
  the template reloads on next entry. NPCs in empty rooms do nothing, which is
  both cheap and — for a world with a doomsday clock — fine lore-wise.

> ⚠️ **Template vs session state** (`DB_SCHEMA.md`, future-consideration #1):
> runtime mutation (opened chest, dead enemy, dropped item) must never write
> back into the `rooms` template row. When live object state needs to survive
> dormancy, it gets a per-session table — the template stays pristine.

---

## Traversal: doors between rooms

A new `Transition` effect joins the closed vocabulary. Walking onto a door
tile (or explicitly using it) resolves to:

1. Validate: is there a connection at this tile? (`room_connections`)
2. Persist the player's durable state (current room, later inventory).
3. Detach from room A — broadcast `player_left` to A.
4. Attach to room B at the connection's arrival tile — broadcast
   `player_joined` to B.
5. Send the traveler a `room_changed` message carrying room B's full state;
   the client re-renders from scratch (same trick as today's state broadcast).

Schema prerequisite (`DB_SCHEMA.md` #7): `room_connections` gains
`to_x`/`to_y` (arrival tile) and a `kind` column (door vs portal).

---

## Timed work — plain asyncio, no scheduler subsystem

**There is no global tick, and no custom scheduler either.** A fixed heartbeat
is a game-engine reflex that's wrong for a world where NPCs mostly stand
around — and a hand-rolled scheduler would just duplicate asyncio's event
loop, which already *is* one. Instead, `Room` grows one small helper:

```python
def schedule(self, delay: float, callback) -> asyncio.Task:
    """Run callback after delay, under this room's lock.
    Tracked in room._pending so dormancy cancels it."""
```

Everything time-driven is a call to it:

- Combat's **round timeout** — `room.schedule(30, self.force_resolve)`,
  cancelled on early resolution. Same logic as today's `main.py` timer, but
  owned by `TurnBasedMode` and per-room (the current single global
  `round_timeout_task` breaks as soon as there are two combat rooms).
- An **NPC decision** — after acting, a brain reschedules itself
  ("consider again in 40–90s"). An idle villager schedules nothing, or a rare
  ambient line. Standing around is *correct behavior*, not a missing feature.
- A **world event** — scripted or generated ("at dusk, the gate closes").

The rules that matter (the helper exists to enforce them):

1. **Timed work belongs to a room** — dormancy cancels the room's `_pending`
   set; nothing ever fires in an empty room.
2. **Callbacks take the room's lock and act through the mode/pipeline**, same
   as a player action — no side-channel mutation.

**The world clock** (VISION.md's 14-day doomsday) needs no ticking either:
persist `world_started_at` and a day length in a small `world_state` table and
*compute* "day 9 of 14" on demand. Lore accumulates as generated content
persisted with provenance, timestamped against that clock.

> **Defer-until-it-hurts:** a real scheduler (an inspectable due-list) earns
> its place only if we later want deterministic time-travel *tests* ("advance
> 3 days, assert the gate closed") or timed events that must *survive
> dormancy/restart* — the latter is a persisted due-list in Postgres, a
> different tool anyway. Neither is near.

---

## The AI generation seams

Three seams, one contract — **AI proposes validated data, the engine disposes**:

| Seam | Trigger | Output | Validated by | Lands in |
|---|---|---|---|---|
| **Rooms** | player reaches a door whose destination doesn't exist yet | terrain grid, objects, enemy spawns, description, connections onward | `level_validation.py` | `rooms` + provenance |
| **NPCs** | room generation includes them; brains run at runtime | NPC definition (stats, personality, goals) + at runtime: proposed `Action`s and dialogue | same handler validation as player actions | `npc_defs` (new) + provenance |
| **Items** | loot, rewards, generation | effect lists from the closed vocabulary | vocabulary schema on insert | `items` (`DB_SCHEMA.md` #22) + provenance |

**Frontier room generation flow:** approach/use an ungenerated door → async
generation kicks off *outside any room lock* → output validated → rejected
output is logged and retried (expect a reject-retry loop; its rate is a metric
worth watching) → accepted output INSERTed with provenance → traversal
proceeds. Until then the door "won't budge — you hear grinding stone" —
latency as flavor. A generated room persists **the moment it's created**;
revisiting is a SELECT, never a regeneration (Part 2: LLM output is not
reproducible).

**NPC brains.** The entity dataclass stays generic — an NPC is an `Entity`
with a `brain_id` reference, zero behavior coupling. The brain is a strategy:

```python
class Brain(ABC):
    def decide(self, view: WorldView) -> Action | None: ...
```

- `ScriptedBrain` first: wander, follow a schedule, bark ambient lines.
- `LLMBrain` second: proposes actions and dialogue **asynchronously** — the
  room never blocks on a model call; the proposal is applied when it arrives
  (under the room lock, validated) or the NPC just idles. NPC-to-NPC dialogue is two brains scheduling replies
  to each other.

Because brain output re-enters through `mode.submit`-equivalent validation, an
LLM-driven NPC is architecturally **just another action submitter** — the
safety story is the one already built.

**Dialogue is content, not effects.** Speech becomes events/messages for the
client; only mechanical consequences (a gift, a wound, a teleport) become
effects. Target vocabulary: `Damage`, `Heal`, `GiveItem`, `Transition`,
`SpawnEntity` — it grows deliberately, never per-piece-of-content.

---

## The client — Vite + TypeScript, DOM grid

**Why DOM, not canvas (decided 2026-07):** this is a text game. Tiles are
styled characters/emoji; hover, click, and tooltips come free; dialogue boxes
and lore panels live in the same technology as the world view; AI-generated
text renders as markup instead of hand-rolled canvas text layout. Canvas buys
animation throughput this game doesn't need, at the cost of rebuilding
hit-testing, text flow, and accessibility from scratch.

> The discipline that keeps DOM fast as the game grows:
> 1. **Build cells once, diff on update.** A broadcast changes a handful of
>    cells — patch those, never rebuild the grid (`innerHTML = ""` dies here).
> 2. **Window large maps.** If rooms outgrow the viewport, render only the
>    visible slice and move a CSS transform for the camera. A decision for
>    when it hurts, not day one.
> 3. **One-way data flow.** socket → store → render. No component touches the
>    socket or another component's state.

```
frontend/
├── net/        socket + message router (join, state_update, room_changed, dialogue)
├── store/      single source of truth: server-state mirror + UI state (armed
│               bomb, open panel), tiny pub-sub — components subscribe
├── grid/       DOM grid renderer; mode-aware input (combat targeting vs walking)
├── ui/         panels: event log, dialogue, inventory, lore journal, room banner
└── main.ts
```

No framework yet: plain TS modules over the store give the unidirectional
discipline without the dependency. If panel complexity later earns React, the
overlay migrates cleanly — it's already isolated from the grid.

**Protocol additions:** state payload gains `mode` and `room_id`;
new `room_changed` (traversal) and `dialogue` messages. Full-state broadcast
per change stays — Part 2 already earmarks incremental updates as a later
optimization.

---

## Milestones

Each is a PR-sized unit with a visible demo at the end:

| # | Milestone | Demo | Notes |
|---|---|---|---|
| A | Engine tests + `Room`/`RoomMode` extraction | combat plays exactly as before | pure refactor; tests land *first* — this moves load-bearing code |
| B | Multi-room runtime + lobby | two combat rooms running concurrently; pick one on join | Part 2's stated MVP flow |
| C | Client rebuild (Vite/TS, DOM grid) | combat UI at parity with today | parallel with B |
| D | `RealtimeMode` + traversal | walk an exploration room, through a door, into a fight, and back | **the vision becomes visible here** |
| E | Frontier room generation | open a door no one has opened; a room that didn't exist is now permanent | first LLM in the loop |
| F | NPCs + items | talk to a scripted villager, then an LLM one; pick up a generated item | brains scripted → LLM; inventory per `DB_SCHEMA.md` #22–24 |

---

## Known risks

- **The mode seam leaks** — mitigated by writing the interface first
  (milestone A) and the tripwire above.
- **LLM latency and cost** — mitigated structurally: generation only at the
  frontier, brains async and lazy, dormant rooms spend nothing.
- **Validation strictness vs generation success** — too strict and every
  generation fails; too loose and garbage lands in the world. Log every
  reject; treat the reject rate as a tuning dial.
- **Single-process ceiling** (Part 2) still applies — accepted; this plan
  makes each room liftable into a worker when the ceiling matters.

---

_Part 1 — the combat engine — lives in [`ARCHITECTURE.md`](ARCHITECTURE.md).
Part 2 — persistence & the multi-process target — in [`BACKEND.md`](BACKEND.md).
The product vision in [`VISION.md`](VISION.md)._
