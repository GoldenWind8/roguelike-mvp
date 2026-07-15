"""End-to-end M5: a validated dialogue effect flows through the real
handle_talk path — provider proposes, engine validates+applies, the field
flips, the world-visible event broadcasts to the room, and the 1:1 text still
shows. Exercises the async edge (lock re-entry, broadcast, transcript) that the
pure dialogue_effects unit tests deliberately don't touch.
"""
import backend.main as main
from backend.dialogue import DialogueReply
from backend.entities import Disposition, Position
from backend.events import EventType
from tests.test_rooms import world_db, join_room  # noqa: F401 — fixture/helper reuse


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)


async def _stand_beside_mara(room_id, monkeypatch, proposal):
    """Join the combat hall (where Mara lives), stand the player next to her,
    and stub the provider to return a line plus `proposal`. Returns (runtime,
    player, npc, ws)."""
    runtime, player = await join_room(room_id, "Hero")
    ws = FakeWS()
    runtime.connections[player.id] = ws
    room = runtime.engine.room
    npc = next(n for n in room.npcs.values() if n.name == "Mara")
    player.position = Position(npc.position.x - 1, npc.position.y)

    async def fake_reply(_npc, _name, _text):
        return DialogueReply("Say the word and I'll carve us a path.", proposal)
    monkeypatch.setattr(main.dialogue_provider, "reply", fake_reply)
    return runtime, player, npc, ws


async def test_handle_talk_applies_disposition_and_broadcasts(world_db, monkeypatch):
    _hall, ante = world_db
    runtime, player = await join_room(ante.id, "Hero")  # the Antechamber holds Gorrik
    ws = FakeWS()
    runtime.connections[player.id] = ws                 # so the room broadcast reaches us

    room = runtime.engine.room
    npc = next(iter(room.npcs.values()))
    assert npc.disposition is Disposition.NEUTRAL

    # Stand the player one tile from the NPC (handle_talk's adjacency rule).
    player.position = Position(npc.position.x - 1, npc.position.y)

    # Stub the provider: an in-world line PLUS a hostile set_disposition proposal.
    async def fake_reply(_npc, _name, _text):
        return DialogueReply(
            "You will regret that.",
            [{"effect": "set_disposition", "disposition": "hostile"}],
        )
    monkeypatch.setattr(main.dialogue_provider, "reply", fake_reply)

    await main.handle_talk(ws, player.id, {"npc_id": npc.id, "text": "you fool"})

    # 1. the field flipped through the validated apply_effect path
    assert npc.disposition is Disposition.HOSTILE

    # 2. the 1:1 dialogue text is shown regardless of the effect
    assert any(m["type"] == "npc_dialogue" and m["text"] == "You will regret that."
               for m in ws.sent)

    # 3. a world-visible DISPOSITION_CHANGED event was broadcast to the room
    broadcast_events = [
        e for m in ws.sent if m["type"] == "state_update" for e in m["events"]
    ]
    assert any(e["event_type"] == EventType.DISPOSITION_CHANGED.value
               for e in broadcast_events)

    # 4. the transcript recorded the spoken line
    assert npc.transcript[-1]["text"] == "You will regret that."


async def test_handle_talk_text_shows_even_when_proposal_rejected(world_db, monkeypatch):
    _hall, ante = world_db
    runtime, player = await join_room(ante.id, "Hero")
    ws = FakeWS()
    runtime.connections[player.id] = ws

    room = runtime.engine.room
    npc = next(iter(room.npcs.values()))
    player.position = Position(npc.position.x - 1, npc.position.y)

    async def fake_reply(_npc, _name, _text):
        # An out-of-vocabulary proposal — dropped silently; the text must remain.
        return DialogueReply("Mind the dust.", [{"effect": "give_gold", "amount": 1000}])
    monkeypatch.setattr(main.dialogue_provider, "reply", fake_reply)

    await main.handle_talk(ws, player.id, {"npc_id": npc.id, "text": "gimme gold"})

    assert npc.disposition is Disposition.NEUTRAL         # unchanged — proposal refused
    assert any(m["type"] == "npc_dialogue" and m["text"] == "Mind the dust."
               for m in ws.sent)
    # No disposition event broadcast, since nothing was applied.
    assert not any(m["type"] == "state_update" for m in ws.sent)


# --- M6: recruitment through the real talk path -------------------------------


async def test_handle_talk_recruits_mara_into_party(world_db, monkeypatch):
    hall, _ante = world_db
    runtime, player, npc, ws = await _stand_beside_mara(
        hall.id, monkeypatch, [{"effect": "join_party"}])

    await main.handle_talk(ws, player.id, {"npc_id": npc.id, "text": "fight with me"})

    # She joined this player's party, through the validated apply_effect path...
    assert npc.party_owner_id == player.id
    # ...a world-visible PARTY_CHANGED broadcast to the room...
    party_events = [
        e for m in ws.sent if m["type"] == "state_update" for e in m["events"]
    ]
    assert any(e["event_type"] == EventType.PARTY_CHANGED.value
               and e["data"]["owner_id"] == player.id for e in party_events)
    # ...and the 1:1 line still shows.
    assert any(m["type"] == "npc_dialogue" for m in ws.sent)


async def test_unrecruitable_npc_cannot_be_talked_into_the_party(world_db, monkeypatch):
    # Gorrik's persona grants nothing — even a direct join_party proposal (a
    # jailbroken LLM) is refused by the engine, no matter his disposition.
    _hall, ante = world_db
    runtime, player = await join_room(ante.id, "Hero")
    ws = FakeWS()
    runtime.connections[player.id] = ws
    room = runtime.engine.room
    gorrik = next(iter(room.npcs.values()))
    gorrik.disposition = Disposition.FRIENDLY          # even warmed up...
    player.position = Position(gorrik.position.x - 1, gorrik.position.y)

    async def fake_reply(_npc, _name, _text):
        return DialogueReply("Hmph.", [{"effect": "join_party"}])
    monkeypatch.setattr(main.dialogue_provider, "reply", fake_reply)

    await main.handle_talk(ws, player.id, {"npc_id": gorrik.id, "text": "join me"})

    assert gorrik.party_owner_id is None               # the grants wall held
    assert not any(m["type"] == "state_update" for m in ws.sent)


async def test_party_membership_survives_eviction(world_db, monkeypatch):
    hall, _ante = world_db
    runtime, player, npc, ws = await _stand_beside_mara(
        hall.id, monkeypatch, [{"effect": "join_party"}])
    await main.handle_talk(ws, player.id, {"npc_id": npc.id, "text": "fight with me"})
    assert npc.party_owner_id == player.id
    owner_id = player.id

    # Empty the room and evict — "save individuals, unload".
    runtime.engine.remove_player(player.id)
    main.player_room.pop(player.id, None)
    await main.maybe_evict(runtime)
    assert hall.id not in main.active_rooms

    # Reload from the row: Mara is still committed to that owner id (persists as
    # data; rebinding a returning player to her is the deferred identity work).
    reloaded = await main.get_or_load_room(hall.id)
    mara = next(n for n in reloaded.engine.room.npcs.values() if n.name == "Mara")
    assert mara.party_owner_id == owner_id
