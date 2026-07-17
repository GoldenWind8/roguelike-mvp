# Emberhollow UI

A React + TypeScript + Vite client for the game, grown out of the UI-arc
design mockup (ROADMAP.md M9/M10). It speaks the backend's real wire protocol
and runs in two modes behind one `GameSocket` seam:

- **Live (default):** connects to the Python server — real accounts, the
  seeded Pillared Hall, turn-based combat, LLM dialogue.
- **Mockup (`?mock`):** the original self-contained tavern demo
  (`src/net/mockSocket.ts`), no backend needed. It emits the same event
  vocabulary as the real engine, so the UI can't tell the difference.

```bash
# terminal 1 — the backend
python -m uvicorn backend.main:app --port 8000

# terminal 2 — this app (dev server proxies /login, /register, /ws to :8000)
cd frontend-react
npm install
npm run dev     # http://localhost:5173  (add ?mock for the tavern demo)
```

Against the live server: **Sign the ledger** to register (3+ char name,
6+ char password), then you wake in The Pillared Hall — combat mode, because
the tenants are home. The session token is kept in localStorage, so a reload
resumes without the form; the ⏻ button forgets it.

Things to try:

- **Fight**: hold the sword (key 1), stand beside a tenant, click it. Combat
  is simultaneous rounds — with other players in the room your action locks
  in ("Committed — waiting for…") until everyone has acted or the timer fires.
- **Talk to Mara** (the sellsword by the south door — stand adjacent, click
  her): real LLM dialogue with a canned fallback. Ask her to fight beside
  you; `join_party` is a validated effect, and she'll actually follow.
- **The doors** (gaps in the north/south walls) lead to the Antechamber,
  where Gorrik sweeps. Insult him at your peril — escalation persists.
- **Dying is a nap**: the client quietly rejoins after the blackout and you
  respawn fresh.
- Music toggle (♪) in the top bar — generative ambient, no audio files.

## The integration seam

- `src/net/types.ts` mirrors the backend contract field-for-field
  (`entities.py`, `room_state.py`, `room_loader.py`, `main.py` handlers,
  `events.py` vocabulary).
- `src/net/socket.ts` is the one interface the app talks through;
  `wsSocket.ts` (live) and `mockSocket.ts` (demo) both implement it, chosen
  by `USE_MOCK` in `store/gameStore.tsx`.
- Auth is the M8 flow: HTTP `/register` / `/login` → signed token →
  `{type:"join", token}` as the first socket message. An `error` with
  `code:"auth"` drops the stored token and returns to the front door.
- **Still mock-only (no backend contract yet):**
  - The belt/inventory. Sword and bomb map onto the real `attack` / `bomb`
    actions; consumables (draught, bread) and the cloak are visual-only
    against the live server and say so when used. Next design conversation:
    an abstract item type server-side.
  - Mock-flavored events (`ambient`, `revive`, `enemy_spawned`, `heal`) —
    the tavern demo's storytelling, never sent by the real engine.

## Layout zones

Status bar (room + live mode, help, music) · left column = at-a-glance status
(You: vigor/armor/strength, Companions) · centre = the room in a player-centred
camera viewport · right column = context (inspection + chronicle, which a
conversation temporarily takes over — the world stays visible while you talk,
so escalation is watchable beside the words; Esc closes the chat) · bottom =
the ten-slot belt.
