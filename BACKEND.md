# Architecture — Part 2: Persistence & Backend

How state outlives a single combat and how the system runs as **more than one process**. Part 1 ([`ARCHITECTURE.md`](ARCHITECTURE.md)) covers the combat engine; this doc covers everything *around* it — the database, the cache/message layer, and the multi-process topology that the room-partitioned world (see [`VISION.md`](VISION.md)) is built on.

> **Legend:** sections marked _(current)_ describe `main` today; _(MVP)_ is the near-term target we're building toward; _(later)_ is deliberately deferred.

---

## Principles

The rules that decide *what* gets stored *where* — so the choice is deliberate, not cargo-culted:

1. **Storage follows access pattern, not hype.** Before picking a technology, ask of each piece of data: how often is it written, how often read, must it survive a crash, who needs to see it? The answer picks the tool. (This is why we have three storage tiers below, not one.)
2. **Infrastructure is added on a trigger, never speculatively.** Each component (Postgres, Redis, queues) earns its place by solving a problem we *actually have*. We name the trigger so we add it on purpose.
3. **The engine owns no database in the hot loop.** Combat resolution reads from memory and stays deterministic. The DB sits at the *edges* of an encounter — supplying shared definitions going in, recording durable outcomes coming out.
4. **Determinism covers the engine, not the LLM.** `world.rng` is reproducible; a language model is not (different output, slow, costs money). So **LLM-generated content is persisted as data** — we store the resolved output, never just a recipe to re-run.

---

## Runtime today & deployment

**Today _(current)_** it's one process: a single FastAPI app (`main.py`) under one `uvicorn`, serving the static frontend and one WebSocket endpoint (`/ws`), with a 30s `asyncio` timer that force-resolves a stalled round. All state lives in one in-memory `WorldState`; a single `asyncio.Lock` serializes every mutation — correct precisely because combat is turn-based and low-frequency. Players join anonymously with a `player_id` and are dropped on disconnect. No DB, no auth, no reconnect.

> ⚠️ **The load-bearing assumption is one *global* world, in-memory.** You can't scale by adding `uvicorn` workers — each would hold its own separate copy — so today one process is the **ceiling on concurrent players**, and any restart drops all live games. That ceiling is a property of the single-world MVP, not the target: the room-partitioned design below makes each room an independent, horizontally-scalable unit. Raising it means moving to that model (the tiers + topology below), not flipping a flag.

**To be genuinely playable _(MVP target)_**, deployment needs:
- **A host** — one always-on container/VM on a managed platform (Fly.io / Render / Railway / a small VPS).
- **HTTPS, so WebSockets become WSS** — the frontend already auto-selects `wss://` on a secure origin, so this is mostly a TLS-terminating proxy concern.
- **Config via env** — Postgres/Redis URLs, RNG seed, timeout — so one image runs locally (point it at SQLite and skip Redis for a minimal dev loop) and in prod.

---

## The three storage tiers

| Tier | What lives here | Access pattern | Technology |
|---|---|---|---|
| **1 · Canonical durable** | generated item configs, generated levels/rooms, the world graph (rooms ↔ doors), players, inventory, event log | write-once or low-frequency, read-many, must survive restarts | **Postgres** |
| **2 · Live session** | the in-progress `WorldState` of an active room | mutated every round, short-lived, reconstructible | **worker memory** |
| **3 · Coordination** | cross-worker messaging, room→worker routing, presence | high-frequency, ephemeral, shared across processes | **Redis** |

The rest of this doc is just these three tiers in detail.

---

## Tier 1 — Postgres: the source of truth _(MVP)_

Everything that must survive a restart and is canonical lives in Postgres.

**Why Postgres from the start (not SQLite):** we've chosen a **multi-process MVP** (many room workers). SQLite is a single-file database that does not tolerate multiple processes writing concurrently — it's the right tool for a single-process app, the wrong one here. Postgres is built for exactly this: many clients, concurrent writes, real transactions.

**Portability — keeping SQLite for tests.** We access the DB through an ORM (**SQLAlchemy**), which generates dialect-appropriate SQL. That keeps **SQLite usable for fast, zero-infra unit tests** even though prod is Postgres — switching is largely a connection-string change.
> ⚠️ **Caveat:** this portability is not free. Postgres-only features (`JSONB` operators, specific types, concurrency semantics) have no SQLite equivalent. Any test that leans on them won't run on SQLite. Rule of thumb: keep *engine-logic* tests DB-agnostic (or DB-free), and let the handful of tests that exercise Postgres-specific queries run against Postgres.

### What we store, and the shapes

| Table (sketch) | Holds | Notes |
|---|---|---|
| `items` | LLM/engine-generated item definitions | `effects` as a **JSON column** — variable shape, but validated against the closed effect vocabulary before insert |
| `rooms` | generated level layouts + metadata | `layout` as JSON; a generated room becomes a persistent row **the moment it's created**, so revisiting = a `SELECT`, not a regeneration |
| `room_connections` | doors/portals between rooms | the **world graph** as edges (`from_room`, `to_room`, …); a plain table, not a graph DB — the graph is small and read-mostly _(later)_ |
| `players` | identity, progression | the anchor for "inventory that follows you between levels" |
| `player_inventory` | what a player owns | references `items`; the cross-room shared state — inventory that follows a player between levels |
| `events` | the append-only `GameEvent` stream | replaying it rebuilds state (see Tier 2's event-sourcing note) |

**Two recurring patterns worth internalizing:**
- **JSON column for variable content, validated on the way in.** Generated items have unbounded *fields*, but their effects come from a *closed vocabulary* (the spine of the project). So the blob is flexible to store yet strict to accept — validate against the vocabulary, then persist.
- **Store the output, plus the seed for provenance.** For LLM content, the resolved config *is* the canonical data. Keep the seed/prompt alongside it for debugging and audit — not as a way to regenerate (you can't, reliably).

---

## Tier 2 — Live session state: worker memory _(current → MVP)_

The active combat `WorldState` stays **in the memory of the worker that owns the room**. It changes every round; putting a database in that loop would be slow and pointless. Because it's deterministic (seed + inputs) it's also *reconstructible* — which gives us freedom in how we treat it:

- **On crash/restart:** rebuild from the persisted event log (Tier 1), not from a live snapshot. Persisting the **event stream** (not just snapshots) is what preserves determinism and replay; periodic snapshots are only an optimization to avoid replaying from round 0, never the source of truth.
- **Ownership:** one room is owned by exactly one worker at a time. This means a room's per-round mutation needs only an **in-process lock** (Part 1's `asyncio.Lock`, now scoped per room) — *not* a distributed lock. The "one global lock" of Part 1 becomes "one lock per room, held by that room's worker."

> This is the payoff of the room model: each worker runs Part-1-style single-threaded combat for the rooms it owns, fully isolated from other workers' rooms.

---

## Tier 3 — Redis: the coordination layer _(MVP)_

Redis is an in-memory, *shared* key-value store. It exists in this architecture for the problems that **only appear once you have more than one process**. It does three jobs here:

1. **Pub/sub — cross-worker message fan-out.** A player's WebSocket is held by *one* worker. When an event must reach a player connected elsewhere (or a player moves between rooms on different workers), workers can't see each other's memory. They publish to a Redis channel; the worker holding that connection is subscribed and forwards it. **This is the core reason Redis is in the MVP** — multi-process + WebSockets makes cross-process messaging unavoidable.
2. **Routing & presence.** A fast shared lookup for "which worker currently hosts room X" and "where is player Y." The gateway uses it to route a newly connecting player to the right worker.
3. **Caching _(later)_.** Hot, frequently-read canonical data (e.g. a popular room's definition) can be cached in Redis to spare Postgres. Not needed for MVP — add it if Postgres read load actually shows up.

> ⚠️ **What Redis is *not* here.** It is not the source of truth — anything in Redis is either ephemeral (presence, routing) or a disposable copy of something authoritative in Postgres. If Redis is wiped, the world survives; only live coordination is disrupted. Keep that line bright: **durable → Postgres, ephemeral/shared-fast → Redis.**

> **Distributed locks?** Not for MVP. Because a room is owned by a single worker, its mutations are serialized in-process. We'd only reach for Redis locks if two workers ever needed to mutate the same resource — which the room-ownership model is specifically designed to avoid.

---

## Topology _(MVP)_

How the processes fit together:

```
   players ──WS──▶ ┌──────────────┐
                   │  Gateway /    │  stable public endpoint; routes a
                   │  Lobby svc    │  connecting player to the worker
                   └──────┬───────┘  hosting their current room
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
    ┌───────────┐   ┌───────────┐    ┌───────────┐
    │room worker│   │room worker│    │room worker│   each OWNS the live
    │ (rooms A) │   │ (rooms B) │    │ (rooms C) │   WorldState of its
    └─────┬─────┘   └─────┬─────┘    └─────┬─────┘   rooms, in memory
          │               │                │
          ├───────────────┴────────────────┤
          ▼                                 ▼
    ┌───────────┐                     ┌───────────┐
    │ Postgres  │                     │   Redis    │
    │ canonical │                     │  pub/sub + │
    │  durable  │                     │ routing/   │
    │  (Tier 1) │                     │ presence   │
    └───────────┘                     │  (Tier 3)  │
                                       └───────────┘
```

- **Gateway/Lobby** owns the world graph and player routing — the thing that, in Part 1's single process, was implicit. It's the answer to "which worker do I send this player to?"
- **Room workers** are horizontally scalable: add more to host more concurrent rooms. Each is a Part-1-style engine for its rooms — shared **Postgres** is authoritative, shared **Redis** is ephemeral.

---

## Identity & persistence implications _(MVP → later)_

Multi-process + persistent players raises the bar Part 1 deliberately left low:

- **Real identity _(MVP-ish)_** — anonymous `player_id` was fine for one ephemeral process. The moment inventory persists in Postgres, a player needs a **stable account** to own it. This is the gate on "inventory follows you between levels."
- **Reconnect/resume** — on a live network drops happen; with routing in Redis, a reconnecting player can be sent back to the worker holding their room within a grace window.
- **Moving between rooms via doors/portals _(later)_** — a player leaving room A (worker 1) for room B (worker 2) is a **handoff**: persist their state, update routing/presence in Redis, attach to the new worker. The world graph (`room_connections`) defines which moves are legal. Explicitly deferred, but the topology above is what makes it tractable.

---

## Tooling _(MVP)_

- **SQLAlchemy** — the ORM / data-access layer. One place that knows about the database; the rest of the code talks to objects. Use its **async** support to fit FastAPI.
- **Schema changes** — recreate tables from the SQLAlchemy models (`create_all`) while data is disposable; run a plain `ALTER TABLE` by hand once there's data worth keeping. A dedicated migration tool earns its place only when there are multiple schema copies to keep in sync — not a starting concern.
- **Connection pooling** — managed by SQLAlchemy; relevant once multiple workers each open connections to Postgres.

---

## Scope & sequence

Each step is downstream of a working effect vocabulary and the Part 1 refactor:

- **NOW** — finish Part 1 (Actions→Effects→Events, `pytest`). No backend work yet.
- **MVP step 1 — persistence foundation** — Postgres + SQLAlchemy (recreate tables from models). First persisted thing: **generated item configs** (Phase 3 in `VISION.md`). Validate-then-store JSON.
- **MVP step 2 — generated rooms** — persist level layouts as rows so rooms are revisitable.
- **MVP step 3 — go multi-process** — gateway/lobby + room workers + Redis pub/sub for cross-worker fan-out and routing. This is where the topology above becomes real.
- **Later** — world graph + door/portal traversal between rooms, full identity/auth, Redis caching if read load demands it.

---

## Known risks (backend)

- **Multi-process is real complexity, early.** Choosing it for the MVP buys horizontal scale but costs a gateway, a message layer, and handoff logic that a single process wouldn't need. Accepted deliberately — but it's the biggest source of "distributed systems" bugs (split brain, stale routing). Keep room ownership strict to contain it.
- **Redis as accidental source of truth.** The easy mistake is letting durable data drift into Redis "because it's there." Guard the durable→Postgres / ephemeral→Redis line.
- **JSON columns hide schema drift.** Flexible storage means the DB won't catch a malformed item — *we* must, via vocabulary validation before insert. The validation is the schema.
- **LLM content is unreproducible.** If a generated row is lost and not backed up, it's gone for good (unlike seeded engine content). Persisted generated content needs **backups** once players depend on it.
- **Test portability isn't automatic.** Postgres-specific queries won't run on SQLite; don't assume a green local SQLite test suite proves prod behavior.

---

_Part 1 — the combat engine — lives in [`ARCHITECTURE.md`](ARCHITECTURE.md). The product vision and the room-partitioned shared world live in [`VISION.md`](VISION.md)._
