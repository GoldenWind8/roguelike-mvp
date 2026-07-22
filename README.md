# Roguelike MMO MVP

A browser-based multiplayer roguelike prototype. The current build is a
server-authoritative persistent room graph: tactical grid combat, free
exploration, and LLM-driven NPCs you can recruit, provoke, or talk down —
with every AI proposal validated by the engine before it touches the world.

The project direction is bigger than the current build: an open-world grid game
where AI helps generate rooms, lore, NPCs, objects, and encounters. The docs are
organized so current reality, near-term MVP work, and future MMO-scale backend
ideas stay separate.

## Current Build

- Browser client served by FastAPI.
- Oakrun Crossroads is the account starting room: a peaceful handcrafted town
  hub with the Great Oak, Wayfarer's Rest, Basil's Cures, carriage yard,
  named residents, and a connected hostile North Road encounter.
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
  and persist across room resets and restarts.
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
- Tests covering database setup, room validation, seeding, room loading, the
  room registry, traversal, NPC persistence, the dialogue provider seam,
  dialogue effects, party members, escalation, and accounts (auth, resume,
  the one-socket rule).

## Not Built Yet

- Room generation (in progress on a parallel track; joins the game once the
  presets are workable).
- Password reset, email verification, login rate limiting, session expiry —
  deferred with named triggers in [Accounts & Identity](docs/archive/ACCOUNTS.md).
- Object pickup, inventory, or object effects.
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
- Click an adjacent player or enemy to attack.
- Hold a belt item (`1`–`0` or click a slot), then click its target — an
  enemy for the sword, a tile for a bomb, yourself for food or a potion.
- Press Space to wait.
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
- [World Object Assets](docs/OBJECT_ASSETS.md): the small data contract for
  multi-tile collision, visual overhang, rendering, and generator policy.
- [NPC And Actor Design](docs/NPCS.md): design source of truth for NPCs,
  actors, dialogue, and followers.
- [Accounts & Identity](docs/archive/ACCOUNTS.md): design source of truth for the
  accounts milestone (M8) — identity, login, persistence.
- [Future Ideas](docs/FUTURE.md): everything deferred — future architecture
  and scale-out, not the next milestone.

Completed or superseded docs live in [docs/archive](docs/archive/) and are
kept for history only.
