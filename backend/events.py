from dataclasses import dataclass
from enum import Enum


class EventType(Enum):
    PLAYER_JOINED = "player_joined"
    PLAYER_MOVED = "player_moved"
    PLAYER_ATTACKED = "player_attacked"
    # Damage lands on players AND enemies through the same effect path, so the
    # event is entity-scoped, not player-scoped (its data key is `target_id`).
    ENTITY_DAMAGED = "entity_damaged"
    PLAYER_DIED = "player_died"
    ENEMY_MOVED = "enemy_moved"
    ENEMY_ATTACKED = "enemy_attacked"
    ENEMY_DIED = "enemy_died"
    # A brain-driven NPC (a follower) moved or struck. Separate from the ENEMY_*
    # pair because names never lie: an ally fighting beside you is not an enemy.
    NPC_MOVED = "npc_moved"
    NPC_ATTACKED = "npc_attacked"
    NPC_DIED = "npc_died"
    # A living actor's disposition toward players changed (e.g. a validated
    # set_disposition dialogue effect). World-visible: broadcast like any event.
    DISPOSITION_CHANGED = "disposition_changed"
    # An NPC's party membership changed (join_party/leave_party effect, or a
    # follower souring out of a party). owner_id is the player id, or null when
    # the NPC left. World-visible like disposition_changed.
    PARTY_CHANGED = "party_changed"
    ROUND_STARTED = "round_started"
    INVALID_ACTION = "invalid_action"
    PLAYER_LEFT = "player_left"
    PLAYER_ENTERED_DOOR = "player_entered_door"
    BOMB_THROWN = "bomb_thrown"


@dataclass
class GameEvent:
    event_type: EventType
    data: dict
    round: int

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "round": self.round,
        }
