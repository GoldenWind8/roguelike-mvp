from abc import ABC, abstractmethod

from backend.actions import ActionType, Action
from backend.effects import apply_effect, compute_damage, Damage
from backend.entities import Disposition, NPC, Position
from backend.inventory import attack_power, attack_range, remove_one
from backend.item_effects import atom_effects
from backend.items import ItemType
from backend.room_state import RoomState
from backend.events import GameEvent, EventType

MOVE_ORDER = 0
ACTION_ORDER = 1

def _invalid(room: RoomState, reason: str) -> GameEvent:
    return GameEvent(EventType.INVALID_ACTION, {"reason": reason}, room.round)

def _manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)

def _yielding_npc_at(room: RoomState, x: int, y: int) -> NPC | None:
    """A living NON-HOSTILE NPC on tile (x, y) — the kind that yields (swaps)
    to a moving player instead of walling them in (NPCS.md Decision 2). Hostile
    actors (enemies, an NPC turned hostile) still block: fight-or-route-around
    friction belongs to THREATS, not to allies, bystanders, or a follower this
    session no longer recognizes as its own (the identity gap — a reconnecting
    player gets a new id, so we intentionally don't gate this on ownership)."""
    occupant_id = room.is_occupied(x, y)
    if not occupant_id:
        return None
    occupant = room.get_entity(occupant_id)
    if (isinstance(occupant, NPC) and occupant.is_alive
            and occupant.disposition is not Disposition.HOSTILE):
        return occupant
    return None

class ActionHandler(ABC):
    # Resolution ordering within a round (moves before acts). Named
    # resolve_order, NOT phase — "phase" already means the engine lifecycle
    # state ("waiting"/"player_phase"), and one word must not mean two things.
    resolve_order: int

    @abstractmethod
    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        pass
    @abstractmethod
    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        pass

class MoveHandler(ActionHandler):
    resolve_order = MOVE_ORDER
    def validate(self, room: RoomState, action : Action) -> GameEvent | None:
            player = room.get_player(action.player_id)
            if not player or not player.is_alive:
                return _invalid(room, f"Player {action.player_id} is not found or dead")
            if not action.direction:
                return _invalid(room, "No direction")
            nx = player.position.x + action.direction[0]
            ny = player.position.y + action.direction[1]
            if not room.is_valid_position(nx, ny):
                return _invalid(room, "Can't move there")
            # A tile is passable if it's empty OR holds a non-hostile NPC (you
            # swap with it on resolve). Hostile actors and other players block.
            if room.is_occupied(nx, ny) and _yielding_npc_at(room, nx, ny) is None:
                return _invalid(room, "Is occupied")
            return None

    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        player = room.get_player(action.player_id)
        old_pos = [player.position.x, player.position.y]
        nx = player.position.x + action.direction[0]
        ny = player.position.y + action.direction[1]
        new_pos = Position(nx, ny)

        # If a non-hostile NPC stands on the target tile, trade places instead
        # of being blocked; otherwise a plain move. (validate already proved the
        # tile is empty or holds a yielding NPC.)
        yielder = _yielding_npc_at(room, nx, ny)
        if yielder is not None:
            room.swap_positions(action.player_id, yielder.id)
        else:
            room.move_entity(action.player_id, new_pos)

        events = [GameEvent(
            EventType.PLAYER_MOVED,
            {"player_id": action.player_id, "from": old_pos, "to": [nx, ny]},
            room.round,
        )]
        if yielder is not None:
            # The displaced NPC slid into the tile you left — narrate it so the
            # log explains why it jumped.
            events.append(GameEvent(
                EventType.NPC_MOVED,
                {"npc_id": yielder.id, "name": yielder.name, "from": [nx, ny], "to": old_pos},
                room.round,
            ))

        # Stepping onto a connected door/portal is intent to traverse. The
        # engine only announces it — the async edge owns the actual transfer
        # (DB load, capacity checks, socket rewiring). Emitting on the step
        # (not on standing) means a denied traversal doesn't retry every round.
        to_room_id = room.template.connections.get((nx, ny))
        if to_room_id is not None:
            events.append(GameEvent(
                EventType.PLAYER_ENTERED_DOOR,
                # name included because by broadcast time the player is no
                # longer in the old room's state for clients to look up.
                {"player_id": action.player_id, "name": player.name,
                 "position": [nx, ny], "to_room_id": to_room_id},
                room.round,
            ))

        return events


class AttackHandler(ActionHandler):
    resolve_order = ACTION_ORDER
    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        player = room.get_player(action.player_id)
        if not player or not player.is_alive:
            return _invalid(room, f"Player {action.player_id} is not found or dead")
        if not action.target_id:
            return _invalid(room, "No target")
        target = room.get_entity(action.target_id)
        if not target or not target.is_alive:
            return _invalid(room, f"Target {action.target_id} is not found or dead")
        # Reach comes from the equipped weapon (a bow strikes across the
        # hall); bare hands and melee weapons keep the old adjacency rule.
        if _manhattan(player.position, target.position) > attack_range(player):
            return _invalid(room, "Target out of reach")
        return None

    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        player = room.get_player(action.player_id)
        target = room.get_entity(action.target_id)
        # Weapon damage replaces bare hands; wearable/potion attack bonuses
        # stack on top (inventory.attack_power) — the delivery half of the
        # weapon type, docs/LOOT.md.
        power = attack_power(player)
        damage = compute_damage(power, target)

        events = [GameEvent(
            EventType.PLAYER_ATTACKED,
            {"attacker_id": player.id, "target_id": target.id, "damage": damage},
            room.round,
        )]
        events.extend(apply_effect(room, Damage(target.id, power, player.id)))
        return events

class WaitHandler(ActionHandler):
    resolve_order = ACTION_ORDER
    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        return None
    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        return []

def _spendable_slot(room: RoomState, action: Action, wanted: ItemType) -> GameEvent | None:
    """Shared consume/throw validation: living player, real slot, right item
    type. Returns the invalid-action event, or None when the slot is good."""
    player = room.get_player(action.player_id)
    if not player or not player.is_alive:
        return _invalid(room, f"Player {action.player_id} is not found or dead")
    if action.slot is None or not (0 <= action.slot < len(player.inventory)):
        return _invalid(room, "No such inventory slot")
    held = player.inventory[action.slot]["item"]
    if held["type"] != wanted.value:
        return _invalid(room, f"{held['name']} is not a {wanted.value}")
    return None


class ConsumeHandler(ActionHandler):
    """Drink/eat slot N: its atoms land on YOURSELF through the same engine
    effects a throwable delivers at range — one vocabulary, two deliveries
    (docs/LOOT.md). Spends one copy from the stack."""
    resolve_order = ACTION_ORDER

    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        return _spendable_slot(room, action, ItemType.CONSUMABLE)

    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        player = room.get_player(action.player_id)
        item = remove_one(player, action.slot)
        events = [GameEvent(
            EventType.ITEM_CONSUMED,
            {"player_id": player.id, "item": item},
            room.round,
        )]
        for effect in atom_effects(player.id, item["payload"]["effects"],
                                   source_id=player.id, source_name=item["name"]):
            events.extend(apply_effect(room, effect))
        return events


class ThrowHandler(ActionHandler):
    """Arc slot N at a tile; its atoms land on every living actor in the
    item's area. The generalization of the old hard-coded bomb: range, area
    and payload are ITEM data now, the resolution loop is unchanged."""
    resolve_order = ACTION_ORDER

    def validate(self, room: RoomState, action: Action) -> GameEvent | None:
        error = _spendable_slot(room, action, ItemType.THROWABLE)
        if error:
            return error
        player = room.get_player(action.player_id)
        if not action.target_tile:
            return _invalid(room, "No target tile")
        tx, ty = action.target_tile
        if not room.is_valid_position(tx, ty):
            return _invalid(room, "Can't target there")
        payload = player.inventory[action.slot]["item"]["payload"]
        if _manhattan(player.position, Position(tx, ty)) > payload["throw_range"]:
            return _invalid(room, "Out of throwing range")
        return None

    def resolve(self, room: RoomState, action: Action) -> list[GameEvent]:
        player = room.get_player(action.player_id)
        item = remove_one(player, action.slot)
        payload = item["payload"]
        tx, ty = action.target_tile
        center = Position(tx, ty)
        radius = payload["area"]["size"]
        events = [GameEvent(
            EventType.ITEM_THROWN,
            {"player_id": player.id, "item": item, "tile": [tx, ty], "radius": radius},
            room.round,
        )]
        # Friendly fire is intentional and GLOBAL to thrown things: atoms land
        # on every living actor in the area, thrower and allies included —
        # what makes throwables tactically interesting. (A per-item "hits"
        # field is the revisit if an item ever needs to discriminate.)
        # Defense/clamp math lives in apply_effect; this loop only emits intent.
        for entity in room.living_actors():
            if _manhattan(center, entity.position) <= radius:
                for effect in atom_effects(entity.id, payload["effects"],
                                           source_id=player.id, source_name=item["name"]):
                    events.extend(apply_effect(room, effect))
        return events


HANDLERS: dict[ActionType, ActionHandler] = {
    ActionType.MOVE: MoveHandler(),
    ActionType.WAIT: WaitHandler(),
    ActionType.ATTACK: AttackHandler(),
    ActionType.CONSUME: ConsumeHandler(),
    ActionType.THROW: ThrowHandler(),
}
