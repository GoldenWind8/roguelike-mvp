"""The closed vocabulary of dialogue effects, and the gate that guards it
(NPCS.md "Dialogue: Two Channels").

This is the engine-disposes half of "AI proposes, the engine disposes". The
dialogue layer hands us RAW, UNTRUSTED proposal dicts — the LLM is an input
source exactly like the client, so a jailbroken NPC can only ever *propose*.
`validate_proposal` is the only path from a proposal to a trusted engine
`Effect`; anything it refuses is dropped (and logged for tuning), while the
NPC's spoken text still shows regardless.

Pure and I/O-free by design: it reads/mutates room state through the same
`apply_effect` path combat uses, and takes no websocket — so the whole
parse->validate->apply->event pipe is unit-testable without a socket.

M5 vocabulary is one entry, `set_disposition` — the smallest slice that proves
the pipe. Every later effect (give_item, join_party) is a new branch here, not
new machinery.
"""
import logging

from backend.effects import Effect, SetDisposition, apply_effect
from backend.entities import NPC, Disposition
from backend.events import GameEvent
from backend.room_state import RoomState

# Closed set: an effect name the LLM can propose must appear here, or it is
# dropped. Grows by vocabulary, not by mechanism.
CLOSED_VOCABULARY = frozenset({"set_disposition"})


def validate_proposal(proposal: dict, *, npc: NPC, room: RoomState) -> Effect | None:
    """Turn one untrusted proposal dict into a trusted engine `Effect`, or
    return None if it is malformed, unknown, or out of context.

    `set_disposition` targets the NPC's own disposition toward players — a
    global field on the NPC (not per-player), so `room` is enough context and
    no `player` argument is needed. That arrives in M6 with join_party, where
    the effect is genuinely per-player.
    """
    if not isinstance(proposal, dict):
        return None
    name = proposal.get("effect")
    if name not in CLOSED_VOCABULARY:
        return None

    if name == "set_disposition":
        if not npc.is_alive:
            return None
        value = proposal.get("disposition")
        try:
            disposition = Disposition(value)
        except ValueError:
            return None
        return SetDisposition(
            target_id=npc.id,
            disposition=disposition,
            source_id=npc.id,
        )

    return None


def process_proposals(room: RoomState, npc: NPC, proposals: list[dict]) -> list[GameEvent]:
    """Validate each proposal, apply the accepted ones through `apply_effect`,
    log the rejections (DOD: "rejections are logged for tuning"), and return
    the collected events for the caller to broadcast."""
    events: list[GameEvent] = []
    for proposal in proposals:
        effect = validate_proposal(proposal, npc=npc, room=room)
        if effect is None:
            logging.warning(
                "dropped dialogue proposal from %s: %r", npc.id, proposal
            )
            continue
        events.extend(apply_effect(room, effect))
    return events
