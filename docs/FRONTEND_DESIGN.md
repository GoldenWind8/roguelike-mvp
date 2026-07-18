# Frontend Design

This document is the source of truth for the browser client direction.

## Current Client

The canonical client is React, TypeScript, and Vite in `frontend-react/`.
FastAPI serves its production build from `frontend-react/dist`; the Vite dev
server proxies login, registration, and WebSocket traffic to FastAPI. (The
original `frontend/` HTML/JavaScript client and the `?mock` demo socket have
been removed.)

It owns:

- A typed `GameSocket` seam with a live WebSocket implementation.
- Authentication and token-based reconnect state.
- A variable-size DOM grid with movement, attack, bomb, wait, inspection, and
  dialogue input.
- Room transitions, live combat/exploration mode, party state, event history,
  and the ten-slot belt backed by the server inventory.
- A player-centred camera and responsive panel layout.

The client remains a renderer and input collector. The server owns rules,
validation, combat resolution, room data, and inspection results.

## Current Shape

```text
frontend-react/src/
  net/      socket setup and message routing
  store/    server-state mirror plus local UI state
  grid/     grid renderer and grid input
  ui/       panels for dialogue, inventory, lore, events
  main.tsx
```

Keep server messages boring and structured. The client should not infer game
rules from text.

## Development And Production

- `npm run dev` starts Vite on port 5173 and proxies `/login`, `/register`, and
  `/ws` to FastAPI on port 8000.
- `npm run build` type-checks the client and emits `frontend-react/dist`.
- FastAPI serves that build at `/`, including fingerprinted assets under
  `/assets`.
- The production build is generated rather than committed. A missing build
  returns a setup-oriented 503 response instead of silently serving the legacy
  client.

## Canvas Decision

Canvas is out of scope.

Project note: treat canvas as a "millionaire-budget exception." Only reconsider
it if the project has a much larger rendering budget, dedicated frontend time,
and a proven DOM performance problem.

Reasons:

- Canvas would make layout, hit testing, accessibility, responsive text, and UI
  panels more custom than they need to be.
- The game is grid-first and information-heavy, not animation-first.
- DOM elements are easier to inspect, click, style, and debug while the rules
  are still changing.
- React can handle the expected panels and grid state without requiring a
  rendering engine.

Also defer Phaser, Pixi, and other rendering engines for the same reason.

## Near-Term Rule

Keep the client a typed renderer and input collector. New inventory and object
interaction work must begin with a server-owned contract; the belt must render
authoritative item state rather than inventing gameplay rules locally.
