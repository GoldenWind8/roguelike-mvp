# Loot System

Design source of truth for items, inventory, chests, and the world clock.
Everything here is implemented; each decision records the alternatives that
were rejected and the trigger that would reopen it.

The one-line architecture: **an item is pure data in three layers — identity,
payload, delivery — and the world hands items out through exactly one
function.** Data describes, systems interpret: the same contract pattern as
rooms (`room_validation`), personas (`persona`), and dialogue effects.

## The Three Layers

1. **Identity** — what an item *is*: name, description, rarity, art, type.
   One `items` row (`backend/models.py ItemDef`), no behavior.
2. **Payload** — what it *does*: a validated JSON blob built from a small
   CLOSED vocabulary of effect atoms (`backend/items.py`):
   - `{"kind": "stat_mod", "stat": attack_damage|defense|max_hp, "amount": n,
     "duration_s"?: s}` — timed (consumable/throwable) or while-equipped
     (wearable, no duration).
   - `{"kind": "restore_hp", "amount": n}` — instant heal, clamped to
     effective max_hp.
   - `{"kind": "restore_hunger", "amount": n}` — food value, clamped to the
     hunger ceiling (Decision 5); a silent no-op on actors without a hunger
     meter (enemies) — atoms are generic data, and what one means is the
     target's business.
   - `{"kind": "damage", "amount": n}` — instant damage, routed through the
     engine's `Damage` effect so defense math and death handling are never
     duplicated.
3. **Delivery** — *how and when* the payload applies; the only layer that is
   code, one system per item type:
   - **wearable** — atoms count while `equipped` (read by `effective_stat`).
   - **consumable** — `consume` action applies atoms to yourself.
   - **throwable** — `throw` action applies atoms to every living actor in
     the item's area at a target tile (`{"throw_range", "area": {shape,
     size}}` — the generalized bomb; radius is Manhattan, like the old one).
   - **weapon** — `{"damage", "range"}`; damage REPLACES bare hands (a sword
     is not fists-plus-sword), range >1 is a ranged weapon. One attack path.

At use time the delivery systems translate atoms into trusted engine effects
(`Heal`, `TimedStat`, `Damage` in `backend/effects.py`) via
`backend/item_effects.py` — one translation table. A new atom kind is two
edits: validation in `items.py`, translation in `item_effects.py`.

**Rejected:** `class Wearable(Item)` subclasses with `apply()` methods.
Behavior baked into data classes can't live in a database, can't be
LLM-generated, and every variant becomes a deploy. With the data model, a
legendary flaming sword invented at 2am is a row.

## Decision 1 — Loot is rolled at open time, never at generation time

Chests are dumb objects with a position and one bit of live state (`opened`).
`loot.spawn_loot()` is the ONE entry point for "the world gives a player an
item": weighted rarity roll (default 60/35/5, `config.LOOT_WEIGHTS`) → pool
draw (or LLM mint, Decision 2). Rooms, seeds, and procgen never carry `loot`
lists — the validator now *rejects* a chest that has one.

A chest yields 1–3 finds (`loot.roll_item_count`, weighted 60/30/10 via
`config.CHEST_ITEM_COUNT_WEIGHTS` — a weights table like the rarity roll,
so a vault chest passes richer counts, never a flag). Each find is its own
`spawn_loot` roll and independently gets the LLM-mint chance — `spawn_loot`
itself still hands out exactly ONE item per call, so future single-item
sources (an NPC gift, a boss drop) call it once; the count is the chest
handler's business, not the roll's.

**Taking is a choice, never automatic.** The roll lands IN
`chest.contents`; the opener's client raises the selection popup (one
rarity-lit card per find, each with its own Take button — take what you
want, leave the rest). `take_item {object_id, index, item_id}` takes one
chosen item; `item_id` guards the index against contents shifting under a
stale click (the server refuses — "already taken" — rather than grab the
wrong thing). Re-opening an opened chest answers 1:1 with
`chest_contents` (peeking isn't world-visible), raising the same popup for
whoever comes next; every take broadcasts `chest_looted`, so two players
at one chest watch each other's grabs live. A full pack just means a Take
fails — no forfeit rule needed.

- Weights are DATA passed per call, never boolean flags — a boss chest is
  `spawn_loot(weights=...)`. Future loot sources (NPC gifts, `on_death`
  drops) call this same function.
- Authored rooms may also pass their stable `room_content_id`. Drazna rolls
  then choose its five regional items 72% of the time when both a matching
  regional item and an ordinary item exist at the rolled rarity. The other
  28% remains the global/LLM-grown pool; a missing local rarity always falls
  back. Generated rooms have no authored content id and retain ordinary loot.
- Regional definitions are idempotently backfilled by their bundled URL-art
  marker even when a world already has items. They are scoped out of
  non-Drazna draws, so the migration neither replays the starter batch nor
  dilutes unrelated player-grown pools.
- First-to-open triggers the roll (decided under the state lock by flipping
  `opened` before the slow roll) and gets first pick; anything left waits
  in `chest.contents` for anyone adjacent to take.
- Chest state PERSISTS in `object_instances` (this was originally ephemeral
  with a revisit-trigger; the trigger fired): every open and take writes
  through via `object_store.py`, and `load_room` overlays the saved state
  back. An opened chest is opened forever — room-cycling can't re-arm it.
- Opening is a REQUEST outside the action economy (the `talk` pattern): it
  needs the DB and maybe an LLM, so it can't run inside synchronous round
  resolution. Standing at a chest mid-combat is its own punishment.

## Decision 2 — LLM minting happens AT open time, with a hard fallback

Small chance (`LOOT_LLM_CHANCE`, default 10%) that the premium tier invents
a never-before-seen item of the rolled rarity (`backend/item_gen.py`). The
wait is deliberate suspense. Requirements that make it safe:

- **The fallback is sacred**: timeout, transport error, malformed JSON,
  validation reject — ALL degrade silently to a pool draw. A chest never
  breaks because an API is down, and the player can't tell a fallback from
  a normal find.
- **AI proposes, the engine disposes**: output is clamped into the rarity's
  power band (`items.clamp_item` — magnitude is forgiven, shape never is),
  then passes the same `validate_item` gate as hand-authored seeds. The
  roll's rarity is forced; the model can't upgrade itself.
- **Everything minted joins the global pool** (`origin="llm"`), so the item
  universe grows permanently and future chests can draw past inventions.
- Measured on a reasoning-model binding (gemini-pro): ~1.1–1.3k hidden
  thinking tokens before ~200 tokens of JSON, with high variance — hence
  `LOOT_LLM_MAX_TOKENS=4096` and `LOOT_LLM_TIMEOUT=20s`. A slow binding
  mostly falls back; bind a faster model to `premium` or raise the knobs.

## Decision 3 — One global wall-clock world clock

Consumable buffs and thrown debuffs carry real-seconds durations
(`duration_s`), stamped as absolute `expires_at` against `world_clock.now()`
(wall time). One clock across both timing modes: a 60s potion lasts 60s
whether rounds resolve fast or slow. Chosen for simplicity over per-mode
fairness — revisit-trigger: if round-combat buff fairness ever feels bad,
durations become round-counts in combat.

- Expiry is LAZY: pruned wherever stats are read, plus a coarse global
  ticker (`WORLD_TICK_INTERVAL`, 2s) that tells clients. NO per-effect
  asyncio timers, ever.
- `active_effects` live on **Actor**, not Player — a poison flask debuffs a
  goblin through exactly the machinery that buffs you.
- Effects are session-scoped (not persisted); gear is forever.
- Hunger (Decision 5) is this ticker's second tenant.

## Decision 4 — Effective stats, never mutated bases

Base stat fields on an actor never change. `inventory.effective_stat(actor,
stat)` = base + equipped-gear atoms + unexpired timed effects, computed at
every read site (`compute_damage`, attack resolution, actor phase, healing
clamps). `to_dict` reports effective values — clients never see a number
combat won't honor. Rejected: mutate-on-equip, because any double-apply or
save/load bug corrupts a character permanently; recomputing from data can't
drift.

When a max_hp ceiling drops (unequip, buff expiry), hp is clamped at that
moment — one rule for both paths (`inventory.clamp_hp`).

## Decision 5 — Hunger: Minecraft's carrot, Don't Starve's stick

A 0–100 meter on players (`Player.hunger`, persisted on `players.hunger` at
the usual edges), advanced by the world ticker in `backend/hunger.py` — pure
functions over room state; the ticker owns *when*, the module owns *what a
tick means*:

- **Drain**: full → empty in ~15 minutes of play (`HUNGER_DRAIN_PER_S`),
  and ONLY while connected and alive — offline time costs nothing (logging
  in starved would punish having a life; Minecraft pauses, this is our
  equivalent). Death resets the belly with the body.
- **Well fed** (≥ `HUNGER_REGEN_THRESHOLD`, 80): wounds knit — 1 hp per
  tick through the ordinary `Heal` effect, each hp costing extra hunger
  (`HUNGER_REGEN_COST`). A full belly is a healing resource you spend.
- **Starving** (0): the meter eats you — `HUNGER_STARVE_DAMAGE` per tick
  through the ordinary `Damage` effect, whose min-1 clamp means armor can't
  make starvation free and whose death handling means it CAN kill you.
  `player_starving` is emitted once at the crossing, not every tick.
- **Eating**: the `restore_hunger` atom on consumables (bread, cheese, stew,
  the Feast) and throwables (lob an ally lunch; wasted on the hunger-less).
  Same translation table as every atom (`item_effects.py` → `RestoreHunger`).
- On **Player**, not Actor: enemies are fungible and hunt nobody's larder.
  The effect no-ops on hunger-less targets, so the atom stays generic.

**Rejected:** hunger as a timed `stat_mod` (it isn't a stat read by combat —
it's a resource with thresholds); per-player drain timers (the one coarse
ticker already sweeps every room); draining by wall-clock while offline (see
above). Revisit-triggers: if starvation death feels cheap in round combat,
pause drain during combat rounds; if players ignore food, steepen the drain
or the starve damage — all three knobs are config data.

## Inventory Rules

10 slots (`config.INVENTORY_SLOTS`); a slot is `{"item": <snapshot>,
"quantity", "equipped"}` on the Player, persisted whole on
`players.inventory` at the usual edges (disconnect/shutdown). All rules live
in `backend/inventory.py`:

- Consumables/throwables stack (unlimited, one slot per distinct item id);
  weapons/wearables never stack.
- Equipped gear stays in its slot, highlighted — at most ONE weapon
  (equipping a new one auto-unequips the old; the only sane answer to
  "which weapon attacks?"), any number of wearables.
- Held items are DENORMALIZED SNAPSHOTS (`items.item_view`) — the hot loop
  and the client never join against the items table, and pool rows are
  immutable once minted so copies can't drift. New power = new rows.
- The pack survives death (dying costs your position, not your stuff).
  Revisit-trigger: a drops-on-death design goal.
- Equip/unequip are free requests outside the round economy (instant gear
  fiddling); consume/throw are real round actions. Revisit-trigger:
  mid-combat armor swapping getting abusive.

## Known accepted costs

- Everyone's inventory rides in the room broadcast (players are a small
  co-op group). Privacy trim = move packs to per-player messages.
- Thrown friendly fire is global and intentional — it makes throwables
  tactically interesting. A per-item `"hits"` field is the escape hatch.
- No line-of-sight on ranged weapons or throws; walls don't block arrows.
  Revisit at the first room where that's abusable.
- Ground/tile effects (fire burning on a tile) are NOT covered by the throw
  model — that's a separate ground-effects system if ever wanted.
- `art` is a typed reference `{"kind": "emoji"|"url", "value"}` so the
  image-gen swap is an addition, not a migration. The LLM currently *picks*
  an emoji; it never draws.

## Wire surface (client contract)

- Actions: `consume {slot}`, `throw {slot, target_tile}` (the `bomb` action
  is gone — bombs are common throwable items now).
- Requests: `open_chest {object_id}` (roll or claim), `equip {slot}`,
  `unequip {slot}`.
- Requests: `take_item {object_id, index, item_id}` (the popup's Take
  button); an opened chest answers `open_chest` with a 1:1
  `chest_contents {object_id, items}` message instead of rolling again.
- Events: `chest_opened {items: [{item, minted}]}` (all still in the chest;
  the opener's client renders the selection popup), `chest_looted {item}`
  (one chosen take), `item_generated`,
  `item_equipped/unequipped/consumed/thrown`, `entity_healed`,
  `hunger_restored {amount, hunger}`, `player_starving` (once, at the
  crossing), `effect_applied/expired`.
- Object summaries carry `opened` + `contents_count`; inspection carries
  full `contents`.
- Player state carries `hunger` (rounded) + `max_hunger` alongside the
  effective stats.
