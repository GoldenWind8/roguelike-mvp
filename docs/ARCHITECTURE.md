# Architecture — Part 1: Combat System

The map of how combat works today and where it's going. This is the engine's first and current focus; the open-world mode is deliberately **out of scope here** (see [`VISION.md`](VISION.md) for the vision and [`WORLD.md`](WORLD.md) for its architecture).

---

## Principles

The rules that keep the engine small as combat grows richer:

1. **Server-authoritative** — the client sends *intent* and renders state; it never decides outcomes.
2. **One source of truth** — `WorldState` owns all state. Systems read and mutate it; they never hold their own.
3. **Small closed core, open content edge** — the engine understands a *handful* of effect primitives. Items, abilities, and enemies are built from those, never by widening the core.

---

## Dependency layers

Strict one-directional imports — a module never reaches for something above it:

```
config · entities · actions · events     leaf   — pure dataclasses & enums
            ↓
world.py                                  state  — WorldState, the source of truth
            ↓
effects.py        (core starts here)      logic  — effect primitives + apply_effect
            ↓
handlers.py                               logic  — one handler per action type
            ↓
systems.py                                logic  — round resolution + enemy AI
            ↓
game.py  →  main.py                       round lifecycle  →  WebSocket / asyncio
```

**Rule of thumb:** if a module needs something from a layer *above* it, push that thing *down* instead. (That's why `apply_effect` lives in `effects.py`: both `handlers.py` and `systems.py` use it, so it must sit below both — otherwise they'd import each other in a cycle.)

---

## The combat round

Simultaneous, phase-based turns:

1. **Player phase** — all living players submit actions; the server waits for everyone (or a 30s timeout). **Moves resolve first** (submission order breaks ties), **then attacks** resolve against post-move positions.
2. **World phase** — each enemy chases the nearest player (Manhattan distance) and attacks if adjacent. No waiting.
Note: In future World phase can include other things like, LLM controlled entity making a move, NPC helper making a move, a world event like a dragon appearing



Then the server broadcasts the full new state plus the round's events; the client re-renders from scratch.

```
Client ──"action"──▶ main.py ──▶ Game.submit_action ──▶ WorldState.pending_actions
                                        │ (all submitted, or timeout)
                                        ▼
                                  resolve_round ──▶ broadcast state + events ──▶ all clients
```

---

## The core design: Actions → Effects → Events _(current — landed in #28)_

This is the heart of Part 1. It splits one tangled responsibility into three clean ones.

| Term | What it is | Example |
|---|---|---|
| **Action** | A player's *intent* for the round | "attack enemy_1", "throw bomb at (4,4)" |
| **Effect** | An atomic *mutation* — the closed vocabulary | `Damage(enemy_1, 5)` |
| **Event** | A *record* of what happened, sent to clients | `PLAYER_DAMAGED`, `ENEMY_DIED` |

> **Actions are unbounded; effects are a small closed set.** An action's job is to produce a list of effects; one applier applies them. A bomb isn't a new *kind* of thing — it's the same `Damage` emitted once per entity in range.

**The payoff:** adding an action = **a new handler + one registry line.** `resolve_round` and `apply_effect` never reopen.

| Action | Resolves into |
|---|---|
| Attack | `[Damage(enemy, 5)]` |
| Bomb (area effect) | `[Damage(e1, 8), Damage(e2, 8), …]` |
| Potion _(future)_ | `[Heal(ally, 10)]` |

---

## Validation: advisory vs authoritative

Because turns are simultaneous, state can change between submit and resolve (a target moves or dies).

- **At submission** — `validate_player_action` gives the player quick feedback ("not adjacent"). Advisory only.
- **At resolution** — every handler **re-checks** its preconditions and silently does nothing if the action is no longer legal. This is the authoritative gate.

---

## Enemy AI _(current)_

Deliberately simple, lives in `systems.resolve_enemy_phase`: find nearest player → if adjacent, attack → else step toward them (within chase range). Enemy damage flows through the same `apply_effect` choke point as players, so combat math stays consistent across both.

---

## Future considerations
- **Single global game** — one `Game`, one world, one `asyncio.Lock`. Fine for now; multiple rooms is a later, deliberate step. It also caps concurrent players to one process.
- **Restart drops live games** — in-memory state means every deploy/crash ends all active sessions. Accepted for the MVP; first thing persistence fixes.
- **No reconnect on a live network** — a dropped WebSocket currently removes the player for good (see Identity). Decide if the MVP needs grace-window resume.
- **Full-state broadcast + full re-render** — simple and robust (~2KB/round); richer combat visuals (area effects, status icons) will eventually want incremental rendering.

---