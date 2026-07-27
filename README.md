# Roguelike MMO MVP

A browser-based multiplayer roguelike prototype. The current build is a
server-authoritative persistent world: tactical grid combat, free exploration,
procedural frontier growth, authored kingdoms, and living NPCs you can recruit,
provoke, follow, or simply miss. Every AI proposal is validated by the engine
before it touches the world; travel, schedules, consequences, and persistence
remain deterministic.

The project direction is bigger than the current build: an open-world grid game
where AI helps generate rooms, lore, NPCs, objects, and encounters. The docs are
organized so current reality, near-term MVP work, and future MMO-scale backend
ideas stay separate.

## Current Build

- Browser client served by FastAPI.
- Oakrun is an eight-room handcrafted starting region. Its peaceful crossroads,
  orchard, hollow, and tollhouse connect through two loops to the hostile north
  road, old mill, barrow, and severed fieldsite.
- Drazna is a nineteen-room lake kingdom with layered civic, palace, archive,
  thieves' underworld, floodworks, and Undertide routes. Its records establish
  the first verified public account of the black rot without claiming the rot
  began there. A temporary Oakrun door supports direct playtesting while a
  separate frontier gateway exercises the intended procedural discovery loop.
- The frontier grows persistent rooms and connections from authored gateway
  exits. Discovering a regional gateway can open its named carriage stops and
  routes atomically without folding temporary development bridges into the
  procedural graph.
- Authored raster art is used for meaningful actors, enemies, landmarks, and
  placed objects; floors and walls keep the same neutral renderer-owned tiles
  used by generated rooms.
- Real-time updates over one WebSocket endpoint.
- A registry of live rooms: each active room owns its own `RoomEngine`/`RoomState`,
  player sockets, and round timer. Rooms load from the DB on first entry and
  are evicted when the last player leaves.
- Door/portal traversal: walk onto a door and the server moves you (hp intact)
  into the connected room; broadcasts are scoped per room.
- Turn-based combat on a grid: movement, attacks, waiting, item use
  (consume/throw), enemy turns.
- Live room modes: a room is in combat exactly while a living hostile actor
  is present, exploration otherwise — derived continuously, not set at load.
  Peaceful rooms resolve movement immediately; combat rooms run the
  turn-based loop; rooms escalate and de-escalate mid-session and the client
  shows every transition.
- Server-owned action validation and resolution.
- SQLAlchemy-backed room definitions using the local SQLite database.
- Seeded room data with terrain, objects, enemy definitions, spawn points, and
  room connections.
- Server-broadcast room dimensions and object summaries.
- NPC dialogue: NPCs are persistent individuals (their own DB rows — hp,
  position, disposition, and a bounded dialogue memory survive room resets
  and restarts). Talking is a one-on-one panel; replies come from an LLM
  (AI Power Grid) with hand-authored canned lines as the always-available
  fallback.
- Dialogue effects: the LLM's reply carries structured effect proposals from
  a closed vocabulary (`set_disposition`, `join_party`, `leave_party`); the
  engine validates in context and applies through the same effect path combat
  uses. AI proposes, the engine disposes.
- Party members: recruit an NPC through dialogue; followers fight beside you
  (the `Brain` seam — chase and follower behaviors picked from actor data)
  and persist across room resets and restarts. Living owned followers travel
  through room connections with their player.
- Authored NPC knowledge and relationships: Oakrun residents know specific
  people and facts rather than receiving omniscient lore. Edda and Wren are
  recruitable travellers whose goals point toward Drazna and the fieldsite.
- Living-world simulation: persistent people hold private goals, sparse
  deliberation windows, deterministic schedules, memories, relationships,
  beliefs, travel plans, meetings, injuries, deaths, and missable off-screen
  opportunities. Dormant-room catch-up is bounded and gives active rooms
  authority whenever a player is present.
- Drazna's intertwined situations include the Undertide expedition, Mara and
  Alin's political conflict, Nera's omitted names, Vasko's return, Low Lantern
  betrayals, carriage meetings, and Gate Seven's multi-outcome regional climax.
  They are discovered through people, evidence, timing, and consequences
  rather than a quest tracker.
- A player-private World drawer separates heard Rumours, evidence-shaped
  Chronicle entries, and last-observed People records. Private motives and
  unseen condition changes are not exposed by the server.
- Escalation: insult the caretaker and his room flips to combat live; kill or
  parley the last hostile and it returns to exploration.
- Accounts & identity (M8): register/login with username + password (optional
  email) over HTTP, play over WebSocket with a signed session token. A
  returning login is the same player — same room, position, and hp — and
  followers rebind across sessions and server restarts. One connection per
  account; player state saves at the edges (disconnect, shutdown), the same
  rhythm as NPCs.
- Browser rendering for variable-size rooms, room transitions, room metadata,
  and first-pass object inspection, plus a minimal login/register form.
- Exploration shop service: Oakrun General Goods has a small daily selection
  drawn through the loot generator, globally limited stock, transactional
  purchases, and persistent player coin balances.
- Exploration noticeboard service: Oakrun mixes authored town notices with
  globally visible, expiring player messages. Each account may hold one short
  notice on the board and may remove only its own.
- Scheduled carriage services use named stops, operating windows, fares,
  layovers, route danger, persistent travel, and generated frontier waystops.
- Tests cover database setup, region topology, procedural discovery, map
  reachability, combat and loot balance, carriage travel, long-horizon living
  world replay, authored branch exclusivity, player-knowledge privacy, room
  loading, traversal, dialogue effects, parties, escalation, and account
  persistence.

## Not Built Yet

- Production-scale procedural variety, encounter ecology, and remote-region
  discovery weighting beyond the current deterministic frontier generator.
- Password reset, email verification, login rate limiting, session expiry —
  deferred with named triggers in [Accounts & Identity](docs/archive/ACCOUNTS.md).
- General world-object pickup and non-consumable key-item support. Chests,
  shops, inventory use, evidence inspection, and authored interactions are
  already functional.
- Enemy and boss on-death loot hooks are not wired yet; current material
  rewards come from chests, shops, and salvage buyback.
- Multi-process workers, Redis routing, or production-scale MMO infrastructure.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.lock.txt
cd frontend-react
npm ci
npm run build
cd ..
```

On macOS/Linux, activate with:

```bash
source .venv/bin/activate
```

## Run

```powershell
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

FastAPI serves the production React build at
[http://localhost:8000](http://localhost:8000). During frontend development,
run `npm run dev` from `frontend-react` and use
[http://localhost:5173](http://localhost:5173); Vite proxies API and WebSocket
traffic to the backend on port 8000.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
cd frontend-react
npm run build
```

## How To Play

- Arrow keys or WASD to move.
- Press E beside a resident or object to interact; clicking any visible part
  of accepted world artwork works too.
- Press Home (or the ◎ button) to recenter the camera after looking around.
- Press J to open Rumours, People, and the Chronicle; these surfaces show only
  what your character has heard, witnessed, or found.
- Click an adjacent player or enemy to attack.
- Hold a belt item (`1`–`0` or click a slot), then click its target — an
  enemy for the sword, a tile for a bomb, yourself for food or a potion.
- Press Space to wait.
- Inspect a named carriage stop to see only currently known destinations,
  departure timing, duration, danger, and fare.
- The server resolves a round when all living players act, or when the turn
  timeout fires.

## Documentation Map

Each doc has one job:

- [Game Design](docs/GAME_DESIGN.md): the design vision and scope.
- [Roadmap](docs/ROADMAP.md): Now / Next / Later milestones — start here to
  see what's next.
- [Current Architecture](docs/ARCHITECTURE.md): how the app works today —
  runtime, backend boundaries, and persistence.
- [Database Schema](docs/DB_SCHEMA.md): current and planned data model.
- [Frontend Design](docs/FRONTEND_DESIGN.md): the React client structure and
  client/server boundary.
- [Art Direction](docs/ART_DIRECTION.md): the raster-asset boundary, visual
  production workflow, and first Oakrun region plan.
- [Authored Content](docs/CONTENT.md): version-controlled catalogues, database
  ownership, and persistent-versus-respawnable character rules.
- [Oakrun Starting Region](docs/OAKRUN.md): room graph, evidence threads,
  interconnected cast, and content ownership.
- [Drazna Kingdom Chapter](docs/DRAZNA.md): the nineteen-room lake kingdom,
  intertwined situations, Gate Seven outcomes, evidence discipline, and
  procedural release boundary.
- [Living World](docs/LIVING_WORLD.md): sparse NPC deliberation, deterministic
  schedules, memory, rumours, authored consequences, and player knowledge.
- [World Regions](docs/WORLD_REGIONS.md): kingdom identities, roads, carriage
  services, and frontier integration.
- [Traversal](docs/TRAVERSAL.md): room connections, procedural gateways, and
  active-room handoff rules.
- [Loot](docs/LOOT.md): runtime item rolls, regional preferences, persistence,
  and inventory delivery.
- [Exploration Noticeboards](docs/NOTICEBOARDS.md): authored notices, persistent
  player posts, expiry, ownership, and protocol boundaries.
- [World Object Assets](docs/OBJECT_ASSETS.md): the small data contract for
  multi-tile collision, visual overhang, rendering, and generator policy.
- [Exploration Shops](docs/SHOPS.md): daily global stock, coins, purchase
  transactions, and the authored-object interaction boundary.
- [NPC And Actor Design](docs/NPCS.md): design source of truth for NPCs,
  actors, dialogue, and followers.
- [Accounts & Identity](docs/archive/ACCOUNTS.md): design source of truth for the
  accounts milestone (M8) — identity, login, persistence.
- [Future Ideas](docs/FUTURE.md): everything deferred — future architecture
  and scale-out, not the next milestone.

Completed or superseded docs live in [docs/archive](docs/archive/) and are
kept for history only.
