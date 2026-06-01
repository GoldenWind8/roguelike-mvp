# Vision

## In one sentence

A multiplayer roguelike where **AI generates the world's content** — items, characters, events — and a small deterministic engine keeps it all fair and consistent. The engine stays tiny and provably correct; the richness comes from an AI layer that only ever proposes *validated effect-data*, never touching game state directly.

## The founding bet: AI is central

Most roguelikes hand-author their content. The bet here is the opposite: **the content is generated.** Items with unknown effects, characters with their own behavior and dialogue, encounters that didn't exist until a player walked into them.

The thing that makes this *safe* (and the reason the architecture matters) is a hard rule:

> **AI proposes, the engine disposes.** A language model never mutates the world. It returns a list of effects drawn from a closed vocabulary; the server validates, clamps, and applies them. Get that vocabulary right and "AI dungeon master" is a content feature, not an engine rewrite.

This is the spine of the project — not a late-stage add-on. Every architectural decision (the closed effect set, server authority, determinism) exists to make AI-generated content trustworthy.

## Two play styles

The game has two distinct loops, sharing one engine and one world.

### 1. Turn-based combat *(in progress)*
Simultaneous, phase-based tactical combat on a grid. Players submit actions, the server resolves them deterministically, enemies act. This is what we're building now (move / attack / wait → effects → bomb, abilities, items). Combat is where the *rules* live and where determinism matters most.

### 2. Open-world exploration *(future)*
A looser loop: players walk around a shared world, find items, talk to each other, and interact with **AI-made characters** — NPCs that a model gives personality, dialogue, and goals. Less about tactical resolution, more about discovery and social play. Combat encounters can ignite *within* exploration (you wander into a fight), at which point the turn-based loop takes over.

The two styles are not separate games — they're two modes of moving through the same world.

## The world: shared, divided into rooms

Multiplayer means **one shared, persistent world** — not isolated match instances. That world is partitioned into **rooms**: bounded spaces players move between.

- A room might be an **exploration zone** (free movement, social, AI characters) or a **combat encounter** (turn-based resolution kicks in).
- Rooms keep the shared world tractable: the engine only has to resolve what's happening in an active room, and AI content can be generated per-room, on demand, and cached.
- Players coexist in the world but are "present" in a room at a time — this is what lets a real-time-ish overworld and a turn-based fight share one server without one stalling the other.

## Principles that make it possible

1. **Server-authoritative** — the client shows state and sends intent; outcomes are decided server-side. Non-negotiable once AI and other players are involved.
2. **Deterministic core** — all randomness through one seeded RNG; given the same inputs a round always resolves the same way. Enables replay, testing, and trustworthy AI integration.
3. **Closed effect vocabulary** — the engine understands a *handful* of primitives (damage, heal, status, …). Everything else — items, abilities, AI creations — is expressed as combinations of those. This is the wall between the small engine and the unbounded content.
4. **AI proposes, engine disposes** — see the founding bet. The model's output is data to be validated, never code to be trusted.

## Roadmap

AI is the through-line, getting more ambitious each phase — items, then characters, then world.

- **Phase 1 — Decouple actions** *(now, #17/#18)*: effect primitives + `apply_effect`, handler registry, Bomb as proof. **Add `pytest` here** — the moment the logic becomes unit-testable, before complexity compounds.
- **Phase 2 — Combat depth**: heal + status effects (burning, stun), inventory/items, a reusable targeting abstraction. Combat starts to feel like a real roguelike.
- **Phase 3 — First AI content**: the "mystery item" — a handler that asks a model for a list of effect primitives, validated and applied. Proves the AI-proposes-engine-disposes loop end-to-end on the smallest surface.
- **Phase 4 — Open world & rooms**: the exploration loop, the shared world partitioned into rooms, moving between them.
- **Phase 5 — AI characters**: NPCs a model gives personality, dialogue, and goals — the social heart of the exploration mode.

Each phase is downstream of a solid effect vocabulary; we don't pull AI breadth forward before the core that keeps it safe exists.

## Open questions to resolve as we go

- **AI latency vs. play.** Model calls take seconds — fine for generating an item on pickup or an NPC on room-entry, dangerous inside a 30s combat turn. Rule of thumb: **generate at the edges (pickup, room-entry), not in the hot loop**, and cache/record results for determinism and replay.
- **How "live" are AI characters?** Pre-generated personality + scripted effects, or model-in-the-loop dialogue at runtime? Likely a spectrum — start pre-generated, add live where it earns its cost.
- **Persistence.** A shared persistent world eventually needs to survive restarts (no DB today). When, and how much?
- **Identity & presence.** Players moving between rooms in a shared world need stable identity and reconnection — more than the current anonymous-join model.
