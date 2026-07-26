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
    # The room's derived mode flipped (M7 escalation): a hostile appeared and
    # the room is now "combat", or the last hostile fell/parleyed and it is
    # "exploration" again. The client swaps UI on this.
    ROOM_MODE_CHANGED = "room_mode_changed"
    ROUND_STARTED = "round_started"
    INVALID_ACTION = "invalid_action"
    PLAYER_LEFT = "player_left"
    PLAYER_ENTERED_DOOR = "player_entered_door"
    # --- loot system (docs/LOOT.md) ---
    # A chest's contents were rolled (first-to-open): `items` is the list of
    # finds `[{item, minted}]`, all still IN the chest — the opener's client
    # renders it as the selection popup; nothing is taken automatically.
    CHEST_OPENED = "chest_opened"
    # A player chose and took one item out of an opened chest (take_item).
    CHEST_LOOTED = "chest_looted"
    # A never-before-seen item was minted by the premium LLM at open time —
    # its own event because the client gives it fanfare.
    ITEM_GENERATED = "item_generated"
    ITEM_EQUIPPED = "item_equipped"
    ITEM_UNEQUIPPED = "item_unequipped"
    # One globally shared shop slot was bought and removed.
    SHOP_PURCHASED = "shop_purchased"
    ITEM_CONSUMED = "item_consumed"
    # A throwable landed: tile, area, and the thrown item for narration.
    ITEM_THROWN = "item_thrown"
    # An actor healed (restore_hp atom) — damage's happier twin.
    ENTITY_HEALED = "entity_healed"
    # A hungry actor ate (restore_hunger atom): amount + new meter value.
    HUNGER_RESTORED = "hunger_restored"
    # A player's hunger meter hit 0 — emitted at the crossing, not every
    # starvation tick (the ongoing damage speaks through entity_damaged).
    PLAYER_STARVING = "player_starving"
    # A timed stat effect landed on an actor (buff drunk, debuff splashed).
    EFFECT_APPLIED = "effect_applied"
    # A timed stat effect ran out on the world clock.
    EFFECT_EXPIRED = "effect_expired"


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
