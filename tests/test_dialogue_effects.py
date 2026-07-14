"""M5 dialogue effects: the parse -> validate -> apply -> event pipe.

This is the "engine disposes" half of the two-channel design (NPCS.md
"Dialogue: Two Channels"). Everything here runs without a websocket: raw,
untrusted proposal dicts go in; validated GameEvents come out. That the pipe
is provable this way IS the point — the security boundary is a pure function,
not tangled into the socket handler.
"""
import logging

import pytest

from backend.dialogue_effects import (
    CLOSED_VOCABULARY,
    process_proposals,
    validate_proposal,
)
from backend.effects import SetDisposition, apply_effect
from backend.entities import Disposition
from backend.events import EventType
from tests.test_npcs import make_npc, make_room_with_npc  # noqa: F401 — helper reuse


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


def test_closed_vocabulary_is_just_set_disposition():
    # M5 is one effect on purpose — later effects are new entries here, not new
    # machinery. This pins the slice.
    assert CLOSED_VOCABULARY == frozenset({"set_disposition"})


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
