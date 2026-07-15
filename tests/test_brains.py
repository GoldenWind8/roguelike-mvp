"""The Brain seam (ROADMAP M6): behavior chosen from data, resolved through the
shared rules. select_brain dispatches on disposition + party membership, the
ChaseBrain hunts the player's side, and the FollowerBrain defends its owner —
all provable without a socket, like the dialogue-effects pipe.
"""
from backend.brains import (
    AttackIntent,
    ChaseBrain,
    FollowerBrain,
    MoveIntent,
    select_brain,
)
from backend.entities import Disposition, Position
from backend.events import EventType
from backend.room_state import RoomState
from backend.systems import resolve_actor_phase
from tests.test_npcs import make_npc


def _room(make_template, **kw):
    return RoomState(make_template(width=7, height=7, **kw), seed=1)


# --- select_brain: the data-driven dispatch -----------------------------------


def test_select_brain_reads_the_actor_data(make_template):
    npc = make_npc()                                   # neutral, no owner
    assert select_brain(npc) is None                   # a bystander just stands

    npc.disposition = Disposition.HOSTILE
    assert isinstance(select_brain(npc), ChaseBrain)   # hostile -> chase (even an NPC)

    npc.disposition = Disposition.FRIENDLY
    npc.party_owner_id = "player_1"
    assert isinstance(select_brain(npc), FollowerBrain)  # recruited -> follow

    npc.is_alive = False
    assert select_brain(npc) is None                   # the dead don't decide


def test_players_are_never_brain_driven(make_template):
    room = _room(make_template)
    player = room.add_player("Hero")
    assert select_brain(player) is None


# --- ChaseBrain: hostiles target the whole player side ------------------------


def test_hostile_targets_a_follower_not_only_players(make_template):
    room = _room(make_template)
    player = room.add_player("Hero")
    player.position = Position(5, 5)                    # far away
    follower = make_npc(id="npc_1", db_id=1, position=Position(2, 2),
                        disposition=Disposition.FRIENDLY, party_owner_id=player.id)
    room.add_npc(follower)
    enemy = room.add_enemy("Goblin", Position(3, 2), hp=10, attack_damage=1, defense=0)

    intent = ChaseBrain().decide(room, enemy)
    # The adjacent follower is the nearest member of the player's side.
    assert isinstance(intent, AttackIntent) and intent.target_id == follower.id


# --- FollowerBrain: defend-my-owner -------------------------------------------


def test_follower_attacks_an_adjacent_hostile(make_template):
    room = _room(make_template)
    player = room.add_player("Hero")
    follower = make_npc(id="npc_1", db_id=1, position=Position(2, 2),
                        disposition=Disposition.FRIENDLY, party_owner_id=player.id)
    room.add_npc(follower)
    enemy = room.add_enemy("Goblin", Position(3, 2), hp=10, attack_damage=1, defense=0)

    intent = FollowerBrain().decide(room, follower)
    assert isinstance(intent, AttackIntent) and intent.target_id == enemy.id


def test_follower_closes_on_a_distant_hostile(make_template):
    room = _room(make_template)
    player = room.add_player("Hero")
    follower = make_npc(id="npc_1", db_id=1, position=Position(1, 2),
                        disposition=Disposition.FRIENDLY, party_owner_id=player.id)
    room.add_npc(follower)
    room.add_enemy("Goblin", Position(5, 2), hp=10, attack_damage=1, defense=0)

    intent = FollowerBrain().decide(room, follower)
    assert isinstance(intent, MoveIntent) and (intent.to.x, intent.to.y) == (2, 2)


def test_follower_regroups_on_owner_when_no_hostiles(make_template):
    room = _room(make_template)
    player = room.add_player("Hero")
    player.position = Position(5, 3)                    # dist 4 from the follower
    follower = make_npc(id="npc_1", db_id=1, position=Position(1, 3),
                        disposition=Disposition.FRIENDLY, party_owner_id=player.id)
    room.add_npc(follower)                              # no enemies in the room

    intent = FollowerBrain().decide(room, follower)
    assert isinstance(intent, MoveIntent) and (intent.to.x, intent.to.y) == (2, 3)


def test_follower_holds_within_the_leash(make_template):
    # At (or inside) the leash the follower does NOT close in — it stays a step
    # back rather than glued to the owner's tile, so it can't stand in your path.
    room = _room(make_template)
    player = room.add_player("Hero")
    player.position = Position(3, 3)                    # dist 2 == FOLLOW_LEASH
    follower = make_npc(id="npc_1", db_id=1, position=Position(1, 3),
                        disposition=Disposition.FRIENDLY, party_owner_id=player.id)
    room.add_npc(follower)

    assert FollowerBrain().decide(room, follower) is None


# --- the phase end to end -----------------------------------------------------


def test_actor_phase_follower_fights_beside_you(make_template):
    room = _room(make_template)
    player = room.add_player("Hero")                   # spawns at (1, 1); phase needs a live player
    follower = make_npc(id="npc_1", db_id=1, position=Position(2, 2),
                        disposition=Disposition.FRIENDLY, party_owner_id=player.id,
                        attack_damage=5)
    room.add_npc(follower)
    enemy = room.add_enemy("Goblin", Position(3, 2), hp=10, attack_damage=1, defense=0)

    events = resolve_actor_phase(room)

    # The follower struck the enemy through the shared damage path...
    assert any(e.event_type is EventType.NPC_ATTACKED for e in events)
    assert enemy.hp == 5
    # ...and the enemy, targeting the player's side, struck the follower back.
    assert any(e.event_type is EventType.ENEMY_ATTACKED for e in events)
