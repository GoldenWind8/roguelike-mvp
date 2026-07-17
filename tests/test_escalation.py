"""M7 escalation: room mode is a live, derived property.

One predicate (`modes.derive_mode`): combat iff a living hostile is present.
These tests drive the whole loop at the engine seam — no websocket needed:

    insult -> set_disposition -> refresh_mode -> combat (rounds buffer)
    last hostile dies / parleys -> refresh_mode -> exploration (immediate moves)

Dialogue-driven transitions go through `RoomEngine.apply_dialogue_effects` —
the same entry point main.handle_talk calls — so these tests exercise the
production path: untrusted proposals in, effects + mode transition out.
"""
from backend.entities import Disposition, Position
from backend.events import EventType
from backend.modes import derive_mode
from backend.room_engine import RoomEngine
from tests.test_npcs import make_npc, make_persona  # noqa: F401 — helper reuse


def event_types(events):
    return [e.event_type for e in events]


def submit_move(engine, player_id, direction):
    return engine.submit_action(player_id, {"action_type": "move", "direction": direction})


def sour(engine, npc):
    """An insult landing: the untrusted-proposal path the LLM feeds, through
    the engine's out-of-band entry point (effects + mode refresh in one call)."""
    return engine.apply_dialogue_effects(
        npc, [{"effect": "set_disposition", "disposition": "hostile"}], player=None
    )


# --- the predicate itself ----------------------------------------------------


def test_derive_mode_counts_only_living_hostiles(make_template):
    engine = RoomEngine(make_template(enemies=((3, 3),)))
    room = engine.room
    assert derive_mode(room) == "combat"

    # A dead hostile stops counting...
    room.enemies["enemy_1"].is_alive = False
    assert derive_mode(room) == "exploration"

    # ...and a friendly or neutral NPC never counted to begin with.
    room.add_npc(make_npc(disposition=Disposition.FRIENDLY))
    assert derive_mode(room) == "exploration"


def test_follower_is_not_a_hostile(make_template):
    # The predicate reads hostility, not "is anyone armed": a recruited ally
    # in the room keeps it exploration once the fight is over.
    engine = RoomEngine(make_template())
    ally = make_npc(disposition=Disposition.FRIENDLY)
    ally.party_owner_id = "player_1"
    engine.room.add_npc(ally)
    assert derive_mode(engine.room) == "exploration"


# --- escalate ------------------------------------------------------------------


def test_souring_an_npc_escalates_to_combat(make_template):
    engine = RoomEngine(make_template())          # peaceful room
    hero, _ = engine.join("Hero")
    npc = engine.room.add_npc(make_npc())         # neutral bystander
    assert engine.mode_name == "exploration"

    events = sour(engine, npc)

    # One call, whole story: the effect lands, the mode flips, the round opens.
    assert event_types(events) == [
        EventType.DISPOSITION_CHANGED,
        EventType.ROOM_MODE_CHANGED,
        EventType.ROUND_STARTED,
    ]
    assert events[1].data == {"mode": "combat"}
    assert engine.mode_name == "combat"
    assert engine.get_state()["room"]["mode"] == "combat"


def test_escalated_room_buffers_actions_into_rounds(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    engine.join("Slowpoke")
    npc = engine.room.add_npc(make_npc(position=Position(3, 3)))  # off the move path
    sour(engine, npc)

    events, resolved = submit_move(engine, hero.id, [0, 1])

    # Combat timing now: buffered until everyone acts, exactly like a seeded room.
    assert not resolved
    assert hero.id in engine.room.pending_actions


def test_soured_npc_fights_like_an_enemy(make_template):
    # The caretaker you insulted attacks on its turn — its brain is already the
    # ChaseBrain (M6); escalation just makes rounds happen at all.
    engine = RoomEngine(make_template(spawn_points=[(1, 1)]))
    hero, _ = engine.join("Hero")
    npc = engine.room.add_npc(make_npc(attack_damage=3))  # at (1, 2), adjacent
    sour(engine, npc)

    events, resolved = submit_move(engine, hero.id, [1, 0])  # sole player -> resolves

    assert resolved
    assert EventType.NPC_ATTACKED in event_types(events) or EventType.NPC_MOVED in event_types(events)


def test_refresh_mode_is_idempotent(make_template):
    engine = RoomEngine(make_template())
    assert engine.refresh_mode() == []            # no change, no events
    npc = engine.room.add_npc(make_npc())
    assert sour(engine, npc) != []                # flips (and refreshes) once
    assert engine.refresh_mode() == []            # second look: already combat


# --- de-escalate -----------------------------------------------------------------


def test_killing_last_hostile_deescalates_without_reload(make_template):
    engine = RoomEngine(make_template(spawn_points=[(2, 1)], enemies=((2, 2),)))
    hero, _ = engine.join("Hero")
    enemy = engine.room.enemies["enemy_1"]
    enemy.hp = 1                                   # one hit finishes it
    assert engine.mode_name == "combat"

    events, resolved = engine.submit_action(
        hero.id, {"action_type": "attack", "target_id": enemy.id}
    )

    assert resolved
    assert EventType.ENEMY_DIED in event_types(events)
    assert EventType.ROOM_MODE_CHANGED in event_types(events)
    assert engine.mode_name == "exploration"

    # Cleared means explorable NOW: immediate movement, no waiting on rounds.
    move_events, moved = submit_move(engine, hero.id, [0, 1])
    assert moved
    assert EventType.PLAYER_MOVED in event_types(move_events)


def test_parley_mid_combat_deescalates_and_clears_pending(make_template):
    engine = RoomEngine(make_template())
    hero, _ = engine.join("Hero")
    engine.join("Slowpoke")
    npc = engine.room.add_npc(make_npc(position=Position(3, 3)))  # off the move path
    sour(engine, npc)

    # A half-collected round is in flight when the parley lands...
    submit_move(engine, hero.id, [0, 1])
    assert engine.room.pending_actions

    events = engine.apply_dialogue_effects(
        npc, [{"effect": "set_disposition", "disposition": "friendly"}], player=None
    )

    # ...and de-escalation drops it: those intents belonged to a fight that
    # no longer exists. Movement is immediate again.
    assert event_types(events) == [
        EventType.DISPOSITION_CHANGED,
        EventType.ROOM_MODE_CHANGED,
    ]
    assert events[1].data == {"mode": "exploration"}
    assert engine.room.pending_actions == {}
    _, moved = submit_move(engine, hero.id, [0, 1])
    assert moved


# --- doors stay open --------------------------------------------------------------


def test_door_traversal_works_mid_combat(make_template):
    # "Into and out of a fight cuts both ways": escalation never locks a room.
    engine = RoomEngine(make_template(
        spawn_points=[(2, 1)], connections={(2, 0): 99}, enemies=((3, 3),),
    ))
    hero, _ = engine.join("Hero")

    events, resolved = submit_move(engine, hero.id, [0, -1])  # sole player -> resolves

    assert resolved
    door = next(e for e in events if e.event_type is EventType.PLAYER_ENTERED_DOOR)
    assert door.data["to_room_id"] == 99


# --- escalation persists ------------------------------------------------------------


def test_room_reloading_a_soured_npc_wakes_up_combat(make_template):
    # Mirrors get_or_load_room: individuals are added AFTER engine construction,
    # then refresh_mode makes the initial derivation see them. Disposition lives
    # on the NPC row, so a room you soured is combat again on your return.
    engine = RoomEngine(make_template())
    assert engine.mode_name == "exploration"

    engine.room.add_npc(make_npc(disposition=Disposition.HOSTILE))
    engine.refresh_mode()

    assert engine.mode_name == "combat"
