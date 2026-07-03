# Roguelike MMO MVP

A browser-based multiplayer roguelike prototype. The current build is a
server-authoritative tactical combat room, with the first pieces of a persistent
room graph already in place.

The project direction is bigger than the current build: an open-world grid game
where AI helps generate rooms, lore, NPCs, objects, and encounters. The docs are
organized so current reality, near-term MVP work, and future MMO-scale backend
ideas stay separate.

## Current Build

- Browser client served by FastAPI.
- Real-time updates over one WebSocket endpoint.
- One in-memory `Game` with one `WorldState`.
- Turn-based combat on a grid: movement, attacks, waiting, bombs, enemy turns.
- Server-owned action validation and resolution.
- SQLAlchemy-backed room definitions using the local SQLite database.
- Seeded room data with terrain, objects, enemy definitions, spawn points, and
  room connections.
- Tests covering database setup, room validation, seeding, and room loading.

## Not Built Yet

- Walking through doors/portals into another room.
- Exploration mode outside turn-based combat.
- Object inspection or item pickup.
- NPC dialogue.
- Persistent player accounts or inventory.
- Multi-process workers, Redis routing, or production-scale MMO infrastructure.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On macOS/Linux, activate with:

```bash
source .venv/bin/activate
```

## Run

```powershell
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Then open two browser tabs to [http://localhost:8000](http://localhost:8000),
enter a name in each, and play.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## How To Play

- Arrow keys or WASD to move.
- Click an adjacent player or enemy to attack.
- Press `B`, then click a tile, to throw a bomb.
- Press Space to wait.
- The server resolves a round when all living players act, or when the turn
  timeout fires.

## Documentation Map

- [Game Design](docs/GAME_DESIGN.md): the cleaned-up design vision and scope.
- [Roadmap](docs/ROADMAP.md): Now / Next / Later milestones.
- [World Exploration Plan](docs/WORLD_EXPLORATION_PLAN.md): the next coding
  plan after this docs pass.
- [World Architecture Proposal](docs/WORLD.md): larger room/runtime ideas to
  revisit after the simple exploration loop works.
- [Current Architecture](docs/ARCHITECTURE.md): how the app works today.
- [Backend Notes](docs/BACKEND.md): current runtime, persistence, and boundaries.
- [Database Schema](docs/DB_SCHEMA.md): current and planned data model.
- [Future Backend](docs/FUTURE_BACKEND.md): long-term scale architecture, not
  the next milestone.
