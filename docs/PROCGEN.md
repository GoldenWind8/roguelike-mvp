# Procedural Generation: Modes & AI Seams

Source of truth for how rooms get made and where AI plugs in. Code lives in
`backend/procgen/`; the harness is
`uvicorn backend.procgen.playground:app --reload --port 8100`.

## Status

Layer 0 (six code-only presets, one validated contract) done. The three
generation modes below are live in the harness as a proof of concept
(2026-07). Proposal validation/repair is deliberately deferred — see Open
Questions.

## The three modes

A mode is the **caller's choice**, not a property of the room type — same
pattern as actor brains. Cost scales with how much the LLM does, so most
rooms should be mode 0.

| Mode | Code does | LLM does | Cost / room |
|---|---|---|---|
| `code` | everything | nothing | free, instant, deterministic |
| `placement` | geometry, entries, spawns | enemies, loot, room name | one small call |
| `full` | validation only | the entire room dict | one large call, may need retries |

Spawns are **always** code-placed in `placement` mode: where players arrive
is an engine invariant (near an entry, mutually reachable), not flavor.

## The placement proposal schema

The LLM receives the ASCII map, a menu of free reachable tiles
(`placement.candidate_tiles`), the enemy catalog, and optional free-text
direction. It must return exactly:

```json
{
  "name": "The Toll Gate",
  "enemy_spawns": [{"enemy_id": 2, "x": 5, "y": 3}],
  "objects": [
    {"type": "chest", "x": 8, "y": 2, "loot": ["coin"]},
    {"type": "fire_barrel", "x": 3, "y": 6, "hp": 3}
  ],
  "notes": "one sentence on the idea behind the layout"
}
```

Closed vocabularies, same law as dialogue effects: `enemy_id` from
`ENEMY_DEFS`, object types from `ObjectType`, loot from `geometry.LOOT_TABLE`.
`apply_placement` merges the proposal raw; the merged room then faces the real
`validate_room` gate, whose error messages are written to double as LLM repair
prompts. NPCs are a planned addition to this schema once persona generation
exists (`persona.validate_persona` is already the gate for that half).

## Full-authoring contract

Mode `full` has no schema of its own — the room dict **is** the contract and
`room_validation.py` is its enforcement. The prompt (`ai._author_brief`)
restates the validator's rules; anything it gets wrong shows up as a red
banner in the harness, which is the experiment: find out what intricacy an
LLM adds beyond procgen, and how often it breaks.

## Where this runs

`procgen/ai.py` holds the prompts; transport and model selection are shared
with NPC dialogue via `backend/llm.py` (mechanism shared, policy local —
dialogue degrades to canned lines, this harness fails loudly into the status
banner). Callers name a model **tier** (`basic`/`standard`/`premium`), never
a provider: placement defaults to `standard`, full authoring to `premium`
(both selectable per request in the harness), and NPC personas carry an
optional `tier` field. Tiers bind to concrete models in `.env`
(`LLM_<TIER>_MODEL` / `_BASE_URL` / `_API_KEY` / `_AUTH`; unset = the grid).
`PROCGEN_TIMEOUT` / `PROCGEN_MAX_TOKENS` remain task-level knobs.

## Open questions (to settle before this leaves the harness)

- **Repair loop**: on validation failure, feed the gate's error back to the
  LLM for one retry? How many rounds before giving up (suggest: 2)?
- **Mode policy in the real game**: who picks the mode per room — depth-based
  (deeper = fancier), landmark-based (POIs get `placement`), or budget-based?
- **Placement scope**: NPCs and connections (which door leads where) are the
  next candidates for the proposal schema.
- **Determinism**: `code` rooms replay from a seed; AI-touched rooms don't.
  Store AI output as authored data once accepted (generate once, keep forever)?
