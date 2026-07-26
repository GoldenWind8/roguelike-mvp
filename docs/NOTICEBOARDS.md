# Exploration Noticeboards

Noticeboards are the second typed exploration service. They combine
version-controlled world writing with a small persistent player-to-player
message surface, without turning free text into executable game data.

## Current Contract

- A placed room object opts into `"interaction": "noticeboard"`.
- `content/noticeboards.json` binds that stable placed-object id to a board id,
  label, capacity, expiry policy, and authored notices.
- Authored notices remain version-controlled content and never occupy player
  capacity.
- Player notices live in `notice_posts`, are globally visible, and survive room
  eviction and server restarts.
- A player may have one active notice per board. They must remove it before
  posting another.
- Oakrun's board holds at most 20 player notices. Each player notice expires
  seven days after posting.
- Messages are plain text, limited to 500 characters, and rendered by React as
  text. They cannot contain effects, markup, item transfers, or commands.
- Only the author may remove a player notice. Authored notices cannot be
  removed through the play protocol.

Expired rows are pruned lazily when a board is read or written. This follows
the shop restock pattern: the first relevant interaction performs inexpensive
shared-world maintenance, so the feature needs no scheduler.

## Protocol

The client sends:

- `open_noticeboard {object_id}`
- `post_notice {object_id, body}`
- `delete_notice {object_id, notice_id}`

Every mutation revalidates that the living player is still adjacent to the
same authored board. Success returns a fresh `noticeboard_opened` snapshot.
The client never edits its local list optimistically.

## Ownership Boundaries

- `noticeboard_defs.py`: validates authored board policy and fixed notices.
- `notice_store.py`: owns expiry, capacity, authorisation, persistence, and
  wire views.
- `main.py`: proximity validation and WebSocket transport.
- `NoticeboardModal.tsx`: renders authoritative snapshots and collects text.

The database uniqueness constraint on `(board_id, author_player_id)` is the
final authority for one active post per player. The board-wide capacity check
is safe under the current single-process state lock. A multi-worker deployment
must add a board-level transaction lock or move capacity into a counter row.

## Accepted Costs

- An already-open panel does not receive another player's new post live.
  Closing and reopening returns global truth. Add a board-change notification
  only if simultaneous board use becomes common.
- There is no moderation UI yet. Author identity and durable post ids make
  hiding or reporting posts an additive follow-up. A shared deployment should
  add moderation and rate controls before relaxing the one-post rule.
- Player notices are intentionally inert. Quests, trades, and jobs should use
  typed server-owned records rather than extracting promises from free text.
