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

## Next: UI Arc

Goal: the client grows up around the identity M8 established.
- **Milestone 9: Client migration.** Mechanical React/TypeScript/Vite port of
  the existing client — no new features. Trigger and stack are already
  defined in [Frontend Design](FRONTEND_DESIGN.md).
- **Milestone 10: UI design.** The real layout/panel/visual pass on the
  migrated stack, including account surfaces. Needs its own short design doc
  before it starts.

Both milestones consume M8's decisions (auth handshake, reconnect states,
message shapes), which are now settled. M8's client surface stayed a trivial
login form on purpose — the real account UI lands with M10.

## Parallel Track: Room Generation

Being built alongside the arc above (`backend/procgen/`, untracked until
workable): a registry of generator presets behind one validated contract —
the same param schema renders the tuning harness, coerces untrusted input,
and will later be the closed vocabulary an AI config-picker fills. It joins
the roadmap as a milestone (wire an ungenerated exit to generate → validate →
store → load) only once the presets are worth walking through.

## Later: Good Ideas To Defer

These belong in the project, but not before the next milestone works. The
design thinking for all of them lives in [Future Ideas](FUTURE.md):

- Per-room locks and the fuller room runtime architecture.
- Account hardening (password reset, email verification, login rate limiting,
  session expiry — deferred with named triggers in
  [Accounts & Identity](archive/ACCOUNTS.md)).
- Inventory that follows players between rooms.
- Object pickup, opening, destruction, and item effects.
- NPC traversal (followers crossing rooms) and per-player relationships.
- World clock and time pressure.
- Redis routing and pub/sub.
- Gateway/lobby service.
- Multiple room workers.

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
