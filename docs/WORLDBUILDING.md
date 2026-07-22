# Worldbuilding: The Handcrafted Slice

Working guide for authoring the first real content — the lore seed and one
vertical slice of the world — before any traversal generation exists. Kept
deliberately tiny; it grows only when authoring hits a real question.
Companion: [TRAVERSAL.md](TRAVERSAL.md), the parked design this slice exists
to inform.

## The slice (what to build, and nothing more)

One of each tier, connected:

- **1 main town** — handcrafted: layout, shops, a carriage stop (even if it
  goes nowhere yet), 4–6 named NPCs whose dialogue carries lore hooks.
- **1 connector chain** — 2–3 regions from the existing code presets, plus
  one hand-placed spark NPC carrying a rumor that points at the semi-main
  area.
- **1 semi-main area** — authored *as if generated*: passes `validate_room`,
  tier-tagged, references at least two lore entries.

Guardrail: starting a second town before any generation exists is scope
drift — stop and return to the traversal work instead.

## How to plan the lore (method)

Write a world bible small enough to hold in your head, in this order:

1. **One central tension** — the thing every faction has an opinion about.
2. **3 factions** — for each: what they want, what they'll do to get it, and
   how a player first encounters them.
3. **~5 historical beats** — a timeline; each beat must leave a visible scar
   somewhere in the world (a ruin, a law, a grudge).
4. **6–10 named figures**, living or dead — each attached to a faction or a
   beat.
5. **5+ open threads** — unresolved hooks a player could pull.

The one rule: **no orphan lore.** Every entry must be reachable in play —
surfaced by a place, an NPC's dialogue, or a thread. If nothing in the world
can deliver it to a player, it isn't lore yet; it's notes.

## Storage (where lore lives)

Draft in markdown freely, but the **database is canonical** — lore only
humans can read cannot be handed to a generator later. Target shape, one
table:

```
lore_entries:
  id, kind (faction|figure|event|place|rumor|thread),
  name, body,
  links   — JSON array of {kind, id} refs to other entries / regions / NPCs,
  origin  (human|llm),
  status  — threads only: dangling|referenced|resolved
```

Same law as items, personas, and rooms: validated at a gate, origin-tagged,
immutable once accepted. This table is the seed of TRAVERSAL.md's world
ledger. The links are the point — a rumor links to the thread it delivers, a
thread to the place that resolves it, a figure to their faction. Retrieval
(pulling only relevant canon into a prompt) reuses the NPC-memory embedding
approach later; not needed to start.

## Prerequisites (systems the slice needs that don't exist)

Roughly in build order:

1. **Gold + shops.** No currency or trade logic exists today (only a "coin"
   loot item). Needs: gold on the player (decide: a `players` field, or
   coin-as-stacking-item — a field avoids spending 1 of 10 pack slots on
   money), a price on `ItemDef`, buy/sell as requests outside the action
   economy (the `talk`/`open_chest` pattern), and vendor NPCs with stock.
2. **Region identity.** Rooms need to belong to a named, tier-tagged region,
   so the authored slice is a worked example of the tier model rather than
   loose rooms.
3. **Ledger table + gate** (above), with the smallest authoring path that
   works — a seed script is fine.
4. **Carriage request.** Mechanically a portal with a fee and a short delay;
   the delay later becomes the generation window (TRAVERSAL.md).

Everything else — `spawn_region()`, the route director, region minting —
waits for generation proper.
