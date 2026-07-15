"""M5 dialogue effects: the parse -> validate -> apply -> event pipe.

This is the "engine disposes" half of the two-channel design (NPCS.md
"Dialogue: Two Channels"). Everything here runs without a websocket: raw,
untrusted proposal dicts go in; validated GameEvents come out. That the pipe
is provable this way IS the point — the security boundary is a pure function,
not tangled into the socket handler.
"""
import logging

import pytest

from backend.config import PARTY_SIZE_CAP
from backend.dialogue_effects import (
    CLOSED_VOCABULARY,
    process_proposals,
    validate_proposal,
)
from backend.effects import JoinParty, LeaveParty, SetDisposition, apply_effect
from backend.entities import Disposition
from backend.events import EventType
from tests.test_npcs import make_npc, make_persona, make_room_with_npc  # noqa: F401 — helper reuse


def _recruitable_setup(make_template):
    """A room with a friendly, join_party-granting NPC and a player to recruit
    into. Returns (room, npc, player) — the happy-path shape for party tests."""
    npc = make_npc(
        disposition=Disposition.FRIENDLY,
        persona=make_persona(grants=["join_party"]),
    )
    room, npc = make_room_with_npc(make_template, npc=npc)
    player = room.add_player("Hero")
    return room, npc, player


# --- apply_effect: the trusted engine effect ----------------------------------


def test_set_disposition_flips_field_and_emits_event(make_template):
    room, npc = make_room_with_npc(make_template)
    assert npc.disposition is Disposition.NEUTRAL

    events = apply_effect(
        room, SetDisposition(target_id=npc.id, disposition=Disposition.HOSTILE, source_id=npc.id)
    )

    assert npc.disposition is Disposition.HOSTILE
    assert [e.event_type for e in events] == [EventType.DISPOSITION_CHANGED]
    assert events[0].data == {"target_id": npc.id, "disposition": "hostile", "source_id": npc.id}


def test_souring_a_follower_dissolves_the_party(make_template):
    # Invariant: a follower is always friendly. Flipping an ally away from
    # friendly must also drop the party bond (else it would hunt its owner).
    room, npc = make_room_with_npc(make_template, npc=make_npc(disposition=Disposition.FRIENDLY))
    npc.party_owner_id = "player_1"

    events = apply_effect(
        room, SetDisposition(target_id=npc.id, disposition=Disposition.HOSTILE, source_id=npc.id)
    )

    assert npc.party_owner_id is None
    types = [e.event_type for e in events]
    assert types == [EventType.DISPOSITION_CHANGED, EventType.PARTY_CHANGED]


def test_set_disposition_on_dead_target_is_noop(make_template):
    room, npc = make_room_with_npc(make_template)
    npc.is_alive = False

    events = apply_effect(room, SetDisposition(target_id=npc.id, disposition=Disposition.HOSTILE))

    assert events == []
    assert npc.disposition is Disposition.NEUTRAL  # unchanged


def test_set_disposition_on_missing_target_is_noop(make_template):
    room, npc = make_room_with_npc(make_template)
    events = apply_effect(room, SetDisposition(target_id="npc_ghost", disposition=Disposition.HOSTILE))
    assert events == []


# --- validate_proposal: the gate ----------------------------------------------


def test_valid_proposal_becomes_engine_effect(make_template):
    room, npc = make_room_with_npc(make_template)

    effect = validate_proposal(
        {"effect": "set_disposition", "disposition": "friendly"}, npc=npc, room=room
    )

    assert isinstance(effect, SetDisposition)
    assert effect.target_id == npc.id          # set_disposition targets the NPC itself
    assert effect.disposition is Disposition.FRIENDLY
    assert effect.source_id == npc.id


@pytest.mark.parametrize("proposal", [
    {"effect": "give_gold", "amount": 1000},                    # unknown effect name
    {"effect": "set_disposition", "disposition": "ecstatic"},   # value not in the enum
    {"effect": "set_disposition"},                              # missing the arg
    {"disposition": "hostile"},                                 # missing the effect name
    "set_disposition",                                          # not even a dict
    None,
])
def test_invalid_proposal_is_dropped(make_template, proposal):
    room, npc = make_room_with_npc(make_template)
    assert validate_proposal(proposal, npc=npc, room=room) is None


def test_proposal_for_dead_npc_is_dropped(make_template):
    room, npc = make_room_with_npc(make_template)
    npc.is_alive = False
    assert validate_proposal(
        {"effect": "set_disposition", "disposition": "hostile"}, npc=npc, room=room
    ) is None


def test_closed_vocabulary_is_the_m6_set():
    # Each effect is a new ENTRY here, not new machinery. M5 shipped
    # set_disposition; M6 added the party pair. This pins the current slice.
    assert CLOSED_VOCABULARY == frozenset({"set_disposition", "join_party", "leave_party"})


# --- process_proposals: the whole pipe, websocket-free ------------------------


def test_process_proposals_applies_valid_and_logs_rejected(make_template, caplog):
    room, npc = make_room_with_npc(make_template)
    proposals = [
        {"effect": "set_disposition", "disposition": "hostile"},   # valid
        {"effect": "teleport", "to": [0, 0]},                      # junk, dropped
    ]

    with caplog.at_level(logging.WARNING):
        events = process_proposals(room, npc, proposals)

    # Only the valid one landed...
    assert npc.disposition is Disposition.HOSTILE
    assert [e.event_type for e in events] == [EventType.DISPOSITION_CHANGED]
    # ...and the rejection was logged for tuning (DOD point 3).
    assert "teleport" in caplog.text


def test_process_proposals_empty_is_noop(make_template):
    room, npc = make_room_with_npc(make_template)
    assert process_proposals(room, npc, []) == []
    assert npc.disposition is Disposition.NEUTRAL


# --- M6: join_party / leave_party ---------------------------------------------


def test_join_party_happy_path(make_template):
    room, npc, player = _recruitable_setup(make_template)

    effect = validate_proposal(
        {"effect": "join_party"}, npc=npc, room=room, player=player
    )

    assert isinstance(effect, JoinParty)
    assert effect.target_id == npc.id
    assert effect.owner_id == player.id
    assert effect.source_id == npc.id


def test_join_party_requires_a_present_player(make_template):
    # No `player` (owner walked away during the LLM call) -> nothing to join.
    room, npc = make_room_with_npc(
        make_template, npc=make_npc(disposition=Disposition.FRIENDLY,
                                    persona=make_persona(grants=["join_party"])))
    assert validate_proposal({"effect": "join_party"}, npc=npc, room=room, player=None) is None


def test_join_party_requires_grant_capability(make_template):
    # Friendly, willing-looking, but the persona does NOT grant join_party.
    # This is the wall party_policy (a prompt hint) can't be — refused hard.
    npc = make_npc(disposition=Disposition.FRIENDLY, persona=make_persona())  # no grants
    room, npc = make_room_with_npc(make_template, npc=npc)
    player = room.add_player("Hero")
    assert validate_proposal({"effect": "join_party"}, npc=npc, room=room, player=player) is None


def test_join_party_requires_friendly_disposition(make_template):
    npc = make_npc(disposition=Disposition.NEUTRAL, persona=make_persona(grants=["join_party"]))
    room, npc = make_room_with_npc(make_template, npc=npc)
    player = room.add_player("Hero")
    assert validate_proposal({"effect": "join_party"}, npc=npc, room=room, player=player) is None


def test_join_party_rejected_when_already_in_a_party(make_template):
    room, npc, player = _recruitable_setup(make_template)
    npc.party_owner_id = "player_other"          # already committed
    assert validate_proposal({"effect": "join_party"}, npc=npc, room=room, player=player) is None


def test_join_party_enforces_the_size_cap(make_template):
    room, npc, player = _recruitable_setup(make_template)
    # Fill the player's party to the cap with stand-in followers in this room.
    for i in range(PARTY_SIZE_CAP):
        filler = make_npc(id=f"npc_fill_{i}", db_id=100 + i, party_owner_id=player.id)
        room.npcs[filler.id] = filler
    assert validate_proposal({"effect": "join_party"}, npc=npc, room=room, player=player) is None


def test_leave_party_valid_only_when_in_a_party(make_template):
    room, npc, player = _recruitable_setup(make_template)
    # Not in a party yet -> nothing to leave.
    assert validate_proposal({"effect": "leave_party"}, npc=npc, room=room, player=player) is None
    # Now recruited -> leaving validates.
    npc.party_owner_id = player.id
    effect = validate_proposal({"effect": "leave_party"}, npc=npc, room=room, player=player)
    assert isinstance(effect, LeaveParty) and effect.target_id == npc.id


def test_join_party_applies_and_emits_party_changed(make_template):
    room, npc, player = _recruitable_setup(make_template)
    events = process_proposals(room, npc, [{"effect": "join_party"}], player=player)
    assert npc.party_owner_id == player.id
    assert [e.event_type for e in events] == [EventType.PARTY_CHANGED]
    assert events[0].data == {"target_id": npc.id, "owner_id": player.id, "source_id": npc.id}
