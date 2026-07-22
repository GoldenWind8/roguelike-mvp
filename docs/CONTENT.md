# Authored Content and Runtime State

Version-controlled JSON is the source of truth for handcrafted content. The
database owns generated canon and everything that has happened during play.

## Authored files

```text
content/
  actors.json       persistent-character presentation
  enemies.json      simple respawnable enemy definitions and art
  objects.json      ordinary placeable object definitions
  buildings.json    large building/landmark definitions
  npcs.json         authored persistent NPC starting definitions
  world/
    oakrun.json      rooms, placements, spawns, and connections
```

There is no asset lifecycle field. Presence in one of these runtime catalogues
means the content is available to the game. Draft generations and source files
stay outside `content/` and `frontend-react/public/art/`.

## Ownership rule

| Data | Owner |
|---|---|
| Authored room geometry and starting placements | JSON |
| Simple respawnable enemy stats and art | JSON |
| Persistent NPC starting persona and stats | JSON |
| Persistent NPC health, location, memory, disposition, and death | Database |
| Generated NPCs and generated rooms | Database |
| Active rounds and fungible enemy instances | Process memory |

Every authored room has a stable `content_id`. Startup synchronizes only the
room-definition fields from JSON. Player state, NPC instances, dialogue memory,
and object-instance state are not replaced.

Every authored object placement has its own stable `id`. Persisted object state
therefore survives reordering the room's object list.

## Persistent versus fungible characters

Any character the world must remember receives an `npcs` row before players
interact with it. This includes named, talking, recruitable, clue-bearing, or
travelling characters. Their authored JSON supplies initial values; the row
owns subsequent state, including death.

Simple enemies are intentionally fungible. Their definitions live in
`enemies.json`; individual combat instances exist only in an active room and
may respawn when that room resets. If one becomes individually important—for
example, a cured homunculus—it is promoted by creating an `npcs` row with its
current state.

## Production database

Do not commit a production database dump as content. Schema migrations plus the
authored JSON reconstruct definitions; normal database backups protect runtime
history. Generated content is validated once, stored in the database, and is
never dependent on reproducing the same model output later.
