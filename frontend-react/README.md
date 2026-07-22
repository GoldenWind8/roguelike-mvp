# Emberhollow UI

A React + TypeScript + Vite client for the game, grown out of the UI-arc
design mockup (ROADMAP.md M9/M10). It speaks the backend's wire protocol
over a WebSocket — real accounts, the seeded Oakrun region, turn-based
combat, LLM dialogue.

```bash
# terminal 1 — the backend
python -m uvicorn backend.main:app --port 8000

# terminal 2 — this app (dev server proxies /login, /register, /ws to :8000)
cd frontend-react
npm install
npm run dev     # http://localhost:5173
```

**Sign the ledger** to register (3+ char name, 6+ char password), then you
wake at the southern edge of **Oakrun Crossroads**, the peaceful first town.
Walk north through its square to meet the residents, inspect the Great Oak and
other placed landmarks, or take the north door into the first hostile road
encounter. The session token is kept in localStorage, so a reload
resumes without the form; the ⏻ button forgets it.

Things to try:

- **Explore Oakrun**: walk north from the arrival point; the camera follows
  you, and you can drag the room to look around. Click anywhere on a landmark
  to inspect it, press E for the nearest interaction, or Home to recenter.
- **Fight**: take the north door, hold the sword (key 1), stand beside an enemy,
  and click it. Combat
  is simultaneous rounds — with other players in the room your action locks
  in ("Committed — waiting for…") until everyone has acted or the timer fires.
- **Talk to the town**: stand beside Basil, Elowen, Rowan, or another resident
  and click them. Dialogue uses the LLM when configured and lore-aware canned
  lines as the always-available fallback.
- **The north door** leads to the North Road and its first encounter; the
  southern door marks the road back toward Amberfall for a future connection.
- **Dying is a nap**: the client quietly rejoins after the blackout and you
  respawn fresh.
- Music toggle (♪) in the top bar — generative ambient, no audio files.
- On localhost, F2 reveals collision cells separately from artwork bounds for
  asset placement and scale tuning.

## The integration seam

- `src/net/types.ts` mirrors the backend contract field-for-field
  (`entities.py`, `room_state.py`, `room_loader.py`, `main.py` handlers,
  `events.py` vocabulary).
- `src/net/socket.ts` is the one interface the app talks through;
  `wsSocket.ts` implements it.
- Auth is the M8 flow: HTTP `/register` / `/login` → signed token →
  `{type:"join", token}` as the first socket message. An `error` with
  `code:"auth"` drops the stored token and returns to the front door.

## Layout zones

Status bar (room + live mode, help, music) · left column = at-a-glance status
(You: vigor/armor/strength, Companions) · centre = the room in a player-centred
camera viewport · right column = context (inspection + chronicle, which a
conversation temporarily takes over — the world stays visible while you talk,
so escalation is watchable beside the words; Esc closes the chat) · bottom =
the ten-slot belt.
