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

from backend.config import PARTY_SIZE_CAP
from backend.effects import Effect, JoinParty, LeaveParty, SetDisposition, apply_effect
from backend.entities import NPC, Disposition, Player
from backend.events import GameEvent
from backend.room_state import RoomState

# Closed set: an effect name the LLM can propose must appear here, or it is
# dropped. Grows by vocabulary, not by mechanism. M6 adds the party pair.
CLOSED_VOCABULARY = frozenset({"set_disposition", "join_party", "leave_party"})


def validate_proposal(
    proposal: dict, *, npc: NPC, room: RoomState, player: Player | None = None
) -> Effect | None:
    """Turn one untrusted proposal dict into a trusted engine `Effect`, or
    return None if it is malformed, unknown, or out of context.

    `set_disposition` targets the NPC's own mood — global, so `room` is enough.
    `join_party`/`leave_party` are genuinely PER-PLAYER (the NPC follows the one
    it is talking to), so they need `player`; without it they can't validate and
    are dropped. This is why the M5 signature grew a `player` argument here.
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

    if name == "join_party":
        return _validate_join_party(npc=npc, room=room, player=player)

    if name == "leave_party":
        # Quitting only makes sense if the NPC is actually in a party. No
        # disposition or capability gate — an ally is always free to leave.
        if not npc.is_alive or not npc.party_owner_id:
            return None
        return LeaveParty(target_id=npc.id, source_id=npc.id)

    return None


def _validate_join_party(*, npc: NPC, room: RoomState, player: Player | None) -> Effect | None:
    """The engine rules that make recruitment safe (NPCS.md "Followers" DOD):
    a live, WILLING (grants), WARM (friendly), FREE npc joins a present player
    who is UNDER THE CAP. Any miss drops the proposal — declining is just text.
    """
    if player is None or not player.is_alive:
        return None
    if not npc.is_alive:
        return None
    # Hard capability gate: the persona must explicitly grant join_party. This
    # is the wall party_policy (a mere prompt hint) can't be — a jailbroken LLM
    # proposing join_party for an NPC that doesn't grant it gets nothing.
    if "join_party" not in npc.persona.get("grants", []):
        return None
    # Disposition threshold: you recruit friends, not strangers or foes.
    if npc.disposition is not Disposition.FRIENDLY:
        return None
    # Already committed — to this player or another. Idempotent, no thrash.
    if npc.party_owner_id is not None:
        return None
    # Party-size cap for the active group. Living followers traverse with their
    # owner, so that group is co-located and represented by this room.
    owned_here = sum(
        1 for n in room.npcs.values() if n.party_owner_id == player.id
    )
    if owned_here >= PARTY_SIZE_CAP:
        return None
    return JoinParty(target_id=npc.id, owner_id=player.id, source_id=npc.id)


def process_proposals(
    room: RoomState, npc: NPC, proposals: list[dict], *, player: Player | None = None
) -> list[GameEvent]:
    """Validate each proposal, apply the accepted ones through `apply_effect`,
    log the rejections (DOD: "rejections are logged for tuning"), and return
    the collected events for the caller to broadcast. `player` is the recruiting
    owner for party effects — None for the pure, socket-free unit tests that
    only exercise self-targeting effects."""
    events: list[GameEvent] = []
    for proposal in proposals:
        effect = validate_proposal(proposal, npc=npc, room=room, player=player)
        if effect is None:
            logging.warning(
                "dropped dialogue proposal from %s: %r", npc.id, proposal
            )
            continue
        events.extend(apply_effect(room, effect))
    return events
