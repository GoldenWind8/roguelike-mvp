# Accounts & Identity (Milestone 8)

Design source of truth for M8: a returning connection reliably becomes the
same player. Accounts are username + password, with an optional email stored
for the future. This doc is deliberately short — it records the decisions and
the smallest slice; everything else is deferred with a named trigger.

## Decisions

1. **One account = one character.** The `players` row *is* the character.
   Character slots are a later table, not a later migration headache: nothing
   in M8 assumes more than one character per account.
2. **Identity is the row id, never the auth method.** Everything references
   `players.id` (an opaque string UUID). Usernames are for humans and login;
   they can be renamed later without breaking references. `npcs.party_owner_id`
   finally points at something real.
3. **Password hashing: bcrypt.** One dependency, battle-tested, fine at this
   scale. (Argon2 rejected for now: better hash, second dependency, no threat
   model that demands it. Revisit if accounts ever guard real value.)
4. **Email is optional, stored, unverified.** Its only planned use is password
   reset — which is *not* in M8. No verification mail, no reset flow, no email
   uniqueness. It is a nullable column and a form field, nothing more.
5. **Login over HTTP, play over WebSocket.** `POST /register` and `POST /login`
   return a signed session token (itsdangerous-style, server secret). The
   client stores it and presents it as the first WebSocket message; the server
   resolves it to a player row *before* the socket joins a room. (Cookies
   rejected: the token-in-first-message flow keeps the WS handler explicit and
   testable, and the client already has a hello message to extend.)
6. **Second connection for the same account is rejected** with a clear error.
   Simplest rule that prevents two sockets mutating one player. Revisit
   trigger: if refresh-during-play feels broken in practice, switch to
   newest-connection-takes-over.
7. **Persistence at the edges, same as NPCs.** Player state (current room,
   x, y, hp) saves on disconnect and on room eviction — the `npc_store`
   pattern applied to players. No mid-play writes in the hot loop.

## Schema

```text
players
  id             String PK (uuid)
  username       String unique, indexed
  password_hash  String
  email          String nullable
  room_id        String nullable   -- where to respawn the player
  x, y           Integer nullable
  hp             Integer
  created_at     DateTime
```

`npcs.party_owner_id` now holds a `players.id`. It stays a plain String (not a
FK) one more milestone, until we're sure eviction ordering never saves an NPC
before its owner exists; promote it once that's proven.

## Flow

```text
register/login (HTTP) -> signed token -> client stores it
ws connect -> first message carries token -> server loads/creates live player
             from the row (room, position, hp) -> joins room as that identity
disconnect/eviction -> row updated -> next login resumes there
```

Follower rebinding falls out: the loader already restores NPCs with
`party_owner_id`; once player ids are stable across sessions, "your" sellsword
is yours again after a restart — pin it with a test.

## Definition of Done

- Register with username + password (+ optional email); duplicate usernames
  are rejected; passwords are stored only as bcrypt hashes and never logged.
- Log in, play, disconnect, log in again: same room, position, and hp.
- A follower recruited in one session still follows the same account in the
  next session, across a server restart.
- A second socket presenting the same account is refused with a readable error.
- An invalid or missing token cannot join a room.
- The client replaces the name prompt with a minimal login/register form —
  plain HTML/JS, one form; the real account UI waits for the React milestone.

## Deferred (named triggers)

- **Password reset + email verification** — when a real player loses a
  password. The email column is already there.
- **Rate limiting / lockout on login** — when the server is exposed beyond
  friends; a cheap per-username delay is acceptable earlier.
- **Character slots, display names, renames** — when the UI milestone gives
  them a surface.
- **Global party cap** (M6 deferral) — the owner-centric query is now
  possible; do it when a second recruitable NPC makes the cap reachable.
- **Session revocation / expiry** — tokens are long-lived signed values in M8;
  add expiry when accounts guard anything worth stealing.
