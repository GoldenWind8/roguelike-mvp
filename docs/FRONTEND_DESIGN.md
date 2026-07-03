# Frontend Design

This document is the source of truth for the browser client direction.

## Current Client

The current client is still plain HTML, CSS, and JavaScript served by FastAPI.
That is acceptable while the game proves the exploration loop.

It now owns:

- A grid built from server-provided room width and height.
- Room state rendering for name, size, mode, and object count.
- Object markers on the grid.
- A small inspection panel that asks the server for object details.
- Combat input for movement, attacks, wait, and bombs.

The client should remain a renderer and input collector. The server owns rules,
validation, combat resolution, room data, and inspection results.

## Future Stack

When the plain JavaScript file starts fighting the UI, the preferred frontend
stack is:

- React.
- TypeScript.
- Vite.
- DOM-based grid rendering.

React fits the likely UI shape: room view, inspection panel, dialogue panel,
inventory, event log, map, and local selection state. TypeScript fits the
WebSocket message shapes and server state contracts. Vite is enough; this does
not need Next.js.

## Migration Trigger

Do not rewrite the client just because React is nicer.

Move when at least one of these becomes true:

- Dialogue, inventory, inspection, map, and event panels make `game.js` hard to
  reason about.
- WebSocket message shapes need shared types and safer refactors.
- UI state bugs start consuming more time than the migration would.
- The client needs focused component tests.

Until then, improve the current client in place.

## Preferred Shape Later

```text
frontend/
  net/      socket setup and message routing
  store/    server-state mirror plus local UI state
  grid/     grid renderer and grid input
  ui/       panels for dialogue, inventory, lore, events
  main.ts
```

Keep server messages boring and structured. The client should not infer game
rules from text.

## Canvas Decision

Canvas is out of scope.

Project note: treat canvas as a "millionaire-budget exception." Only reconsider
it if the project has a much larger rendering budget, dedicated frontend time,
and a proven DOM performance problem.

Reasoning:

- Canvas would make layout, hit testing, accessibility, responsive text, and UI
  panels more custom than they need to be.
- The game is grid-first and information-heavy, not animation-first.
- DOM elements are easier to inspect, click, style, and debug while the rules
  are still changing.
- React can handle the expected panels and grid state without requiring a
  rendering engine.

Also defer Phaser, Pixi, and other rendering engines for the same reason.

## Near-Term Rule

Build the playable loop first:

1. Door and portal traversal.
2. Exploration movement timing.
3. Server-owned object effects when needed.
4. Basic NPC dialogue.
5. Combat-room integration.

Only then decide whether the current HTML/CSS/JS client has earned a React
migration.
