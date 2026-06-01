# Vision

## In one sentence

A multiplayer roguelike where **AI generates the world's content** — items, characters, events — and a small deterministic engine keeps it all fair and consistent. The engine stays tiny and provably correct; the richness comes from an AI layer that only ever proposes *validated effect-data*, never touching game state directly.

## The founding bet: AI is central

Most roguelikes hand-author their content. The bet here is the opposite: **the content is generated.** Items with unknown effects, characters with their own behavior and dialogue, encounters that didn't exist until a player walked into them.

The thing that makes this *safe* (and the reason the architecture matters) is a hard rule:

> **AI proposes, the engine disposes.** Generating content returns a list of effects drawn from a closed vocabulary; the server validates, clamps, and applies them. Get that vocabulary right and "AI dungeon master" is a content feature, not an engine rewrite.

This is the spine of the project — not a late-stage add-on. Every architectural decision (the closed effect set, server authority) exists to make AI-generated content trustworthy.

## Two play styles

The game has two distinct loops, sharing one engine and one world.

### 1. Turn-based combat *(in progress)*
Simultaneous, phase-based tactical combat on a grid. Players submit actions, the server resolves them deterministically, enemies act.

### 2. Open-world exploration *(future)*
A looser loop: players walk around a shared world, find items, talk to each other, and interact with **AI-made characters** — NPCs that a model gives personality, dialogue, and goals. Less about tactical resolution, more about discovery and social play [Baulders gate/minecraft]. 
Combat encounters can ignite *within* exploration (you wander into a fight), at which point the turn-based loop takes over.

I want this to have a similar flow to fear and hunger termia.
The two styles are not separate games — they're two modes of moving through the same world.

## The world: shared, divided into rooms

MMO means **one shared, persistent world** — not isolated match instances. That world is partitioned into **rooms**: bounded spaces players move between.

- A room might be an **exploration zone** (free movement, social, AI characters) or a **combat encounter** (turn-based resolution kicks in).
- Rooms keep the shared world tractable: the engine only has to resolve what's happening in an active room, and AI content can be generated per-room, on demand, and cached.
- Players coexist in the world but are "present" in a room at a time — this is what lets a real-time-ish overworld and a turn-based fight share one server without one stalling the other.

## Considerations

- **How "live" are AI characters?** Pre-generated personality + scripted effects, or model-in-the-loop dialogue at runtime? Likely a spectrum — start pre-generated, add live where it earns its cost.
