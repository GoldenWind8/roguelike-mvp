# Roadmap

This roadmap keeps three ideas separate:

- **Now**: what exists or is being cleaned up.
- **Next**: the next MVP milestone to build.
- **Later**: important ideas that should not distract the next milestone.

## Now: Current Reality

The current system — room registry, door traversal, live derived room mode
(combat/exploration), NPC dialogue with the validated effect channel, party
members, escalation, and their accepted limitations — is documented in one
place: [Current Architecture](ARCHITECTURE.md). In one line: multiple rooms
can be live in one process, a room is in combat exactly while a living hostile
is present, everything is server-authoritative, and AI proposes while the
engine disposes.

The "NPCs That Matter" arc (Milestones 6–7) is complete: you can recruit a
follower, and talk your way into — and out of — a fight. The detailed
definition-of-done write-ups are archived in
[NPC Arc Milestones](archive/NPC_MILESTONES.md)

Milestone 8 (identity & accounts) is done: username + password login over
HTTP, a signed token as the WebSocket's first message, a `players` table
whose row *is* the character, and followers that rebind across sessions and
restarts. Design source of truth: [Accounts & Identity](archive/ACCOUNTS.md).

## Now: UI Arc Complete

Milestones 9–10 have landed. The React/TypeScript/Vite client speaks the live
backend protocol, includes account surfaces and the panel/grid visual pass,
and its production build is now the client FastAPI serves. The former
HTML/JavaScript client remains only as historical reference. See
[Frontend Design](FRONTEND_DESIGN.md).

## Now: Loot System Complete

The loot arc has landed — the server-owned inventory and object-interaction
contract, implemented end to end. In one line: items are pure data (identity
+ validated payload + delivery system), chests roll their contents at open
time through one `spawn_loot()` entry point, a small chance mints a
never-before-seen premium-LLM item into the growing global pool, timed
effects ride one global wall-clock, and the React client speaks the whole
contract (pack, equip highlights, chest states, buff timers). Design source
of truth, with every decision's rejected alternatives and revisit-triggers:
[Loot](LOOT.md).

Two follow-ons have since landed (LOOT.md updated in place): **hunger**
(Decision 5 — a 0–100 meter drained by the world ticker, Minecraft-style
regen when well fed, Don't-Starve-style starvation damage at zero, food
items carrying the `restore_hunger` atom) and **multi-item chests** (1–3
finds per open through the same `spawn_loot`, revealed to the opener in a
popup).

## Now: Exploration Services Started

Oakrun General Goods is the first typed exploration service: small daily stock
drawn through the existing loot generator, globally bought-up slots, and a
persistent player coin balance. Purchases atomically spend coins, save the
pack, and remove shared stock. See [Exploration Shops](SHOPS.md).

Oakrun's noticeboard is the second service: fixed authored notices share one
panel with globally persistent player messages. Player posts are plain text,
limited to one active message per account, capped per board, author-removable,
and lazily expired after seven days. See
[Exploration Noticeboards](NOTICEBOARDS.md).

## Parallel Track: Room Generation

Being built alongside the arc above (`backend/procgen/`, untracked until
workable): a registry of generator presets behind one validated contract —
the same param schema renders the tuning harness, coerces untrusted input,
and will later be the closed vocabulary an AI config-picker fills. It joins
the roadmap as a milestone (wire an ungenerated exit to generate → validate →
store → load) only once the presets are worth walking through.

## Later: Good Ideas To Defer

These belong in the project, but not before the next milestone works. The
design thinking for most lives in [Future Ideas](FUTURE.md); loot-specific
deferrals carry named revisit-triggers in [Loot](LOOT.md):

- Per-room locks and the fuller room runtime architecture.
- Account hardening (password reset, email verification, login rate limiting,
  session expiry — deferred with named triggers in
  [Accounts & Identity](archive/ACCOUNTS.md)).
- NPC traversal (followers crossing rooms) and per-player relationships.
- Redis routing and pub/sub.
- Gateway/lobby service.
- Multiple room workers.

Loot follow-ons (the system is built to receive these — each is a caller of
existing seams, not a rework; see LOOT.md for the triggers):

- **More loot sources**: NPCs handing over items in dialogue, and enemy
  `on_death` drops — both are one call to `loot.spawn_loot()` with their own
  weights; the `enemy_defs.on_spawn/on_death` hook columns are the waiting
  seam.
- **Depth-scaled rarity**: `LOOT_WEIGHTS` is already a data table passed per
  call — deeper floors pass richer weights when floors exist.
- **Image-gen item art**: swap `art.kind` from `"emoji"` to `"url"` per item
  in `item_gen.py`; the typed reference means no migration, and the client
  already switches on `kind`.
- **Hunger**: done (LOOT.md Decision 5). Remaining clock tenants — torches,
  spoilage — follow the same ticker pattern when wanted.
- **Ground/tile effects** (fire burning on a floor tile): a separate system
  (state on tiles + checks on movement and ticks) — deliberately NOT bolted
  onto the throw model.
- **Persistent chest state** (`object_instances`): DONE (the revisit-trigger
  fired) — chest lifecycle now writes through to the DB at open/take and
  survives room reloads; see DB_SCHEMA.md and LOOT.md.
- **Drops-on-death / stack caps / line-of-sight for ranged & thrown /
  per-item friendly-fire ("hits") / per-player inventory privacy**: each
  deferred with its trigger noted in LOOT.md "Known accepted costs".

## Senior-Dev Rule Of Thumb

Build the smallest version that proves the gameplay loop, then harden the
architecture around the proven loop.

For this project, that means:

1. Keep the current combat engine. (holding)
2. Add room traversal in one process. (done)
3. Add exploration movement timing. (done)
4. Add simple exploration interactions — NPC dialogue. (done)
5. Let dialogue change the world through a validated effect channel, then
   spend that machinery twice: recruitment and escalation. (done)
6. Give players identity: the smallest account system that makes a returning
   connection the same player, unblocking everything that needs an owner. (done)
7. Let the client earn its rebuild: migrate the stack mechanically, then
   design the UI on solid ground.
8. Fold in room generation from the parallel track, and only then decide what
   scale features have earned their complexity.
