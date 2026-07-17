"""Standalone generator harness — run it SEPARATELY from the game.

    uvicorn backend.procgen.playground:app --reload --port 8100
    # then open http://localhost:8100

It imports the real generators and the real validator, but never touches the
DB or the game server — its whole job is a tight feedback loop: pick a type,
turn knobs, hit Generate, see the room and whether it passed the gate.

Three generation modes, mirroring how brains are picked from data (a mode is
the caller's choice, not a property of the room type):

  code       pure procgen — no LLM, no cost. What most rooms should use.
  placement  procgen builds bare geometry (contents zeroed, spawns still
             code-placed), the LLM furnishes it: enemies, loot, a name.
  full       the LLM authors the entire room dict from the contract alone.

AI-mode responses always include the room EVEN WHEN INVALID — seeing what the
LLM got wrong is the point of a harness. Proposal validation/repair is a known
TODO (docs/PROCGEN.md); today the real gate's verdict is simply displayed.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.llm import TIERS
from backend.procgen import generate, list_types, schema_for
from backend.procgen import ai
from backend.procgen.base import ENEMY_NAMES, validate
from backend.procgen.placement import (
    CONTENT_PARAMS, apply_placement, candidate_tiles, shape_params,
)

app = FastAPI(title="Procgen Playground")

_STATIC = Path(__file__).parent / "static"


class GenerateRequest(BaseModel):
    room_type: str = ""
    params: dict = {}
    seed: int = 0
    mode: str = "code"      # code | placement | full
    direction: str = ""     # free-text creative direction for the AI modes
    tier: str = ""          # model tier for AI modes; "" = the mode's default


def _pick_tier(requested: str, default: str) -> str:
    """Coerce an untrusted tier the same way Param.coerce treats knobs:
    unknown values fall back to the default, never to an error page."""
    return requested if requested in TIERS else default


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/api/types")
def api_types() -> dict:
    return {
        "types": list_types(),
        "enemy_names": ENEMY_NAMES,
        "ai_available": ai.available(),
        "content_params": list(CONTENT_PARAMS),
        "tiers": list(TIERS),
        "tier_defaults": {"placement": ai.PLACEMENT_TIER, "full": ai.AUTHOR_TIER},
    }


@app.get("/api/schema/{room_type}")
def api_schema(room_type: str) -> dict:
    return {"params": schema_for(room_type)}


@app.post("/api/generate")
async def api_generate(req: GenerateRequest) -> dict:
    if req.mode == "placement":
        return await _generate_placement(req)
    if req.mode == "full":
        return await _generate_full(req)
    return generate(req.room_type, req.params, req.seed).to_json()


async def _generate_placement(req: GenerateRequest) -> dict:
    """Code shape, AI furnishing. The geometry is still seed-deterministic;
    the LLM's placements are not — that asymmetry is by design (shape is an
    engine guarantee, contents are flavor)."""
    shape = generate(req.room_type, shape_params(req.params), req.seed)
    if not shape.ok:
        return {**shape.to_json(), "mode": "placement"}

    tier = _pick_tier(req.tier, ai.PLACEMENT_TIER)
    try:
        proposal = await ai.propose_placement(
            shape.room, candidate_tiles(shape.room), req.direction, tier=tier)
    except ai.AIError as e:
        # Show the bare geometry anyway — the code half still worked.
        return {**shape.to_json(), "ok": False, "mode": "placement",
                "error": f"AI placement failed: {e}", "tier": tier}

    room = apply_placement(shape.room, proposal)
    error = _gate(room)
    return {"ok": error is None, "room": room, "seed": req.seed,
            "attempts": shape.attempts, "room_type": req.room_type,
            "params": shape.params, "error": error, "mode": "placement",
            "tier": tier, "ai_notes": _notes(proposal)}


async def _generate_full(req: GenerateRequest) -> dict:
    tier = _pick_tier(req.tier, ai.AUTHOR_TIER)
    try:
        room = await ai.design_room(req.direction, tier=tier)
    except ai.AIError as e:
        return {"ok": False, "room": None, "seed": req.seed, "attempts": 1,
                "room_type": "full_ai", "params": {}, "mode": "full",
                "error": f"AI design failed: {e}", "tier": tier}
    error = _gate(room)
    return {"ok": error is None, "room": room, "seed": req.seed, "attempts": 1,
            "room_type": "full_ai", "params": {}, "error": error,
            "mode": "full", "tier": tier, "ai_notes": _notes(room)}


def _gate(room: dict) -> str | None:
    """The real gate, hardened for AI output: a malformed dict can raise
    TypeError/KeyError inside the validator (it was written for dicts that are
    at least room-shaped), and here that's a verdict, not a crash."""
    try:
        return validate(room)
    except Exception as e:
        return f"room is not even room-shaped: {type(e).__name__}: {e}"


def _notes(proposal: dict) -> str:
    notes = proposal.get("notes")
    return notes.strip() if isinstance(notes, str) else ""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100)
