# Traversal System (parked design)

**Status: design notes only — nothing here is implemented, and everything is
subject to change.** Parked deliberately until the handcrafted world slice
ships (see [WORLDBUILDING.md](WORLDBUILDING.md)); that slice is expected to
revise this doc, not obey it. Treat every statement below as the current
leaning, recorded so the thinking survives, not as a contract.

Origin: a notebook sketch (2026-07) — main towns, connector regions,
semi-main areas — refined against the loot and procgen precedents.

## The three tiers

Every place has a tier. Tier is data on the region, and it drives generation
cost, danger, and narrative weight all at once.

| Tier | What it is | Generation | Procgen mode |
|---|---|---|---|
| **main** | Large town/castle. Safe haven: shops, NPCs, transport out (carriage), lore-dense, exploration-only. | Handcrafted | none — authored rows |
| **semi-main** | The destination at the end of a path. Complex, dangerous, worth the trip. | Code + LLM | `placement` (occasionally `full`) |
| **connector** | Travel filler between places: caves, open land. | Code only, rare LLM spark | `code` |

This answers PROCGEN.md's open "mode policy" question: **landmark-based — the
tier selects the mode.** Expensive LLM calls concentrate exactly where players
slow down and pay attention; free deterministic generation covers the ground
they merely cross. Traversal tiers and procgen modes are one system: the tier
is the policy that picks the mode.

## Principles

- **PCG owns space and fairness; the LLM owns meaning.** Geometry,
  reachability, spawn safety, danger budget: code, always (spawns are already
  an engine invariant even in `placement` mode). Names, purpose, who lives
  here, what it references: LLM, fed from the ledger. The LLM never draws
  tiles; at most it picks which preset/config fits the story.
- **One entry point.** `spawn_region()` becomes to places what
  `loot.spawn_loot()` is to items: the single seam where tier policy, canon
  lookup, and validation live. Carriage, door, and portal all call it.
- **Weights are data, never flags.** A tier is a weights table (mode chance,
  danger, POI count, spark chance) passed per call — a connector is
  `spawn_region(weights=CONNECTOR)`, never `is_connector=True`.
- **The fallback is sacred.** LLM failure during generation degrades silently
  to `code` mode with a pool-drawn name. A road never breaks because an API
  is down, and the player can't tell.
- **Clamp to the tier's danger band.** Same law as `clamp_item`: magnitude is
  forgiven, shape never. The LLM cannot put a deathtrap on a connector road.

## The world ledger

Generated content feels random not because generators are bad but because
each generation is **stateless** — it references nothing the player has seen,
and nothing later references it. The fix is a shared canon layer both PCG and
the LLM read from and write to.

The item pool already proves this at small scale: minted items are validated,
origin-tagged, immutable, join the global pool, and recur in future chests —
which is exactly why they don't feel like throwaway filler. The ledger is the
same shape with more row kinds: `faction`, `figure`, `event`, `place`,
`rumor`, `thread`. Two additions items never needed:

- **Links between rows** — a rumor points at a thread, a thread at the place
  that resolves it, a figure at their faction.
- **Thread state** — `dangling → referenced → resolved`. A generated ruin
  isn't random if it resolves thread #12 the player heard in a tavern.

Read path: region generation pulls relevant canon (controlling faction, two
open threads the player has brushed) into the prompt/config. Write path:
accepted output becomes canon — "generate once, keep forever" extended from
tiles to meaning.

**Connector sparks are thread carriers.** The rare connector NPC isn't
flavor; it's a cheap delivery vehicle for a rumor about the semi-main area
ahead — the boring travel leg does foreshadowing for almost nothing.
Rejected: a flat 1% roll (pure RNG starves or clumps); prefer a budget — at
least one spark per connector chain. Revisit-trigger: playtesters can't
recall meeting anyone between towns.

## Eager topology, lazy content

Loot's Decision 1 ("roll at open time") ports to places with one split:

- **Topology is eager**: the graph edge, the destination's tier, and its
  canon bindings are decided when the edge is created — so a rumor can point
  at a place before anyone has been there.
- **Content is lazy**: tiles, spawns, and population roll at arrival or
  boarding, first-visitor-triggers-the-roll, exactly like a chest.

Items never needed foreshadowing; places do. If everything about a
destination waited for arrival, there would be nothing for a spark NPC to
foreshadow.

## Transport cost = generation budget

**The carriage ride is the loading screen.** LOOT.md treats the LLM mint
delay as deliberate suspense; a carriage is the same trick at region scale —
boarding to an ungenerated destination buys a diegetic window to run the
expensive route generation while scenery rolls by. Doors must resolve
instantly, and `code` mode is instant, so the axes align by construction:
cheap traversal = cheap generation, slow traversal = premium generation. The
transport mode and the generation budget are the same dial.

## Open forks (to be settled by the handcrafted slice)

1. **Topology** — one hub town with a widening frontier, or multiple main
   towns with connector webs between them (the sketch's "carriage → other
   town" hints at the latter)? Drawing the slice's actual map decides this.
2. **Plot source** — leaning: a thin authored world bible plus emergent
   threads. Rejected leaning: pure emergence (lore soup with no spine).
   Writing the bible (WORLDBUILDING.md) settles it.
3. **Granularity** — lazy one-region-at-a-time, or a director that plans the
   whole route (connector chain + destination) as one narrative unit at
   boarding time? Leaning: route-as-unit — pacing (foreshadow → travel →
   payoff) is a property of the journey, not any single region, and
   roll-at-boarding is LOOT.md Decision 1 at region scale. It is also the
   bigger build.

## Why this is parked: handcraft first

You cannot automate the production of something you have never produced
once — a generator is a formalization of what good content looks like, and
none exists yet for towns or semi-main areas. Precedent, twice over: the item
pool started as hand-authored seeds and LLM minting arrived later as a second
author writing into the same pool; procgen Layer 0's code presets preceded
every LLM mode.

The condition that keeps the handcrafted phase from being throwaway:
**handcraft through the contracts.** Authored regions pass `validate_room`,
authored NPCs pass the persona gate, authored lore lands in ledger rows
(`origin="human"`). Then adding generation later adds an *author*, not a
system.
