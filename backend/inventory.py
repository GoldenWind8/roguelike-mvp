"""The pack and what carrying it does to you (docs/LOOT.md).

Every inventory rule lives HERE, as pure functions over the slot list on a
Player — entities.py stays behavior-free data. The rules, from the design:

  - 10 slots (config.INVENTORY_SLOTS). A slot is
    {"item": <items.item_view dict>, "quantity": int, "equipped": bool}.
  - Consumables/throwables STACK (unlimited quantity, one slot per distinct
    item id); weapons/wearables never stack.
  - Equipping never leaves the slot — `equipped` flips true and the item
    highlights in place. Multiple wearables may be equipped at once; at most
    ONE weapon (equipping a weapon silently unequips the previous one — the
    only sane answer to "which weapon does an attack use?").
  - Consuming/throwing decrements quantity; the slot vanishes at zero.

Also here: EFFECTIVE STATS. Base stat fields on an Actor never change —
equipment (players) and timed active_effects (any actor: potions buff you,
a poison flask debuffs a goblin) are summed at every read site instead.
Mutating base stats on equip was rejected because every save/load or
double-apply bug would corrupt a character permanently; recomputing from
data can't drift.
"""
from backend.config import INVENTORY_SLOTS
from backend.entities import Actor, Player
from backend.items import equipable, stackable
from backend import world_clock


# --- adding and removing ------------------------------------------------------


def add_item(player: Player, item: dict) -> int | None:
    """Put one copy of `item` (an item_view dict) into the pack. Returns the
    slot index, or None when the pack can't take it (full, and no stack to
    join) — the caller decides what a refusal means (chest keeps it)."""
    if stackable(item["type"]):
        for i, slot in enumerate(player.inventory):
            if slot["item"]["id"] == item["id"]:
                slot["quantity"] += 1
                return i
    if len(player.inventory) >= INVENTORY_SLOTS:
        return None
    player.inventory.append({"item": item, "quantity": 1, "equipped": False})
    return len(player.inventory) - 1


def remove_one(player: Player, index: int) -> dict | None:
    """Take one copy out of slot `index` (a consume/throw spends it, a future
    drop discards it). Returns the item_view, or None on a bad index. The
    slot disappears when its last copy goes."""
    slot = _slot(player, index)
    if slot is None:
        return None
    item = slot["item"]
    slot["quantity"] -= 1
    if slot["quantity"] <= 0:
        player.inventory.pop(index)
    return item


def _slot(player: Player, index) -> dict | None:
    if not isinstance(index, int) or not (0 <= index < len(player.inventory)):
        return None
    return player.inventory[index]


# --- equipping ----------------------------------------------------------------


def equip(player: Player, index: int) -> str | None:
    """Flip a slot to equipped. Returns an error string for the client, or
    None on success. One weapon at a time: equipping a weapon auto-unequips
    the current one (multiple WEARABLES are fine — pile on the armor)."""
    slot = _slot(player, index)
    if slot is None:
        return "No such inventory slot"
    if not equipable(slot["item"]["type"]):
        return f"You can't equip a {slot['item']['type']}"
    if slot["equipped"]:
        return "Already equipped"
    if slot["item"]["type"] == "weapon":
        for other in player.inventory:
            if other["equipped"] and other["item"]["type"] == "weapon":
                other["equipped"] = False
    slot["equipped"] = True
    return None


def unequip(player: Player, index: int) -> str | None:
    slot = _slot(player, index)
    if slot is None:
        return "No such inventory slot"
    if not slot["equipped"]:
        return "That isn't equipped"
    slot["equipped"] = False
    # Taking off +max_hp gear can strand hp above the new ceiling — clamp at
    # the moment the ceiling moves, the one place it can happen silently.
    clamp_hp(player)
    return None


def equipped_weapon(player: Player) -> dict | None:
    """The payload of the one equipped weapon, or None (bare hands)."""
    for slot in player.inventory:
        if slot["equipped"] and slot["item"]["type"] == "weapon":
            return slot["item"]["payload"]
    return None


# --- effective stats ----------------------------------------------------------


def effective_stat(actor: Actor, stat: str, now: float | None = None) -> int:
    """base + equipment (players) + unexpired timed effects (any actor) — the
    ONE answer to "how hard does this thing hit / hold / heal". Every combat
    read site uses this; nothing ever writes the base fields."""
    if now is None:
        now = world_clock.now()
    prune_expired(actor, now)
    value = getattr(actor, stat)
    if isinstance(actor, Player):
        for slot in actor.inventory:
            if slot["equipped"]:
                for atom in slot["item"]["payload"].get("effects", []):
                    if atom["kind"] == "stat_mod" and atom["stat"] == stat:
                        value += atom["amount"]
    for effect in actor.active_effects:
        if effect["stat"] == stat:
            value += effect["amount"]
    return value


def attack_power(player: Player, now: float | None = None) -> int:
    """What an attack lands with: the equipped weapon's damage REPLACES the
    bare-hands base (a sword is not fists-plus-sword), then attack_damage
    bonuses from wearables and potions add on top."""
    weapon = equipped_weapon(player)
    base = weapon["damage"] if weapon else player.attack_damage
    return base + (effective_stat(player, "attack_damage", now) - player.attack_damage)


def attack_range(player: Player) -> int:
    """Melee reach 1 unless the equipped weapon says farther (Hunter's Bow).
    No line-of-sight check yet — walls don't block arrows (revisit-trigger:
    the first room where that's abusable)."""
    weapon = equipped_weapon(player)
    return weapon["range"] if weapon else 1


# --- timed effects (the world-clock tenants) ----------------------------------


def add_timed_effect(actor: Actor, stat: str, amount: int, duration_s: float,
                     source: str, now: float | None = None) -> None:
    if now is None:
        now = world_clock.now()
    actor.active_effects.append({
        "stat": stat, "amount": amount,
        "expires_at": now + duration_s, "source": source,
    })


def prune_expired(actor: Actor, now: float | None = None) -> list[dict]:
    """Drop expired effects, returning what fell off (the ticker turns those
    into EFFECT_EXPIRED events). Lazy by design — see world_clock.py."""
    if now is None:
        now = world_clock.now()
    expired = [e for e in actor.active_effects if e["expires_at"] <= now]
    if expired:
        actor.active_effects = [e for e in actor.active_effects if e["expires_at"] > now]
        # A lapsed +max_hp buff can strand hp above the ceiling, same as
        # unequipping armor — one clamp rule for both paths.
        clamp_hp(actor, now)
    return expired


def clamp_hp(actor: Actor, now: float | None = None) -> None:
    ceiling = effective_stat(actor, "max_hp", now)
    if actor.hp > ceiling:
        actor.hp = ceiling


def active_effects_view(actor: Actor, now: float | None = None) -> list[dict]:
    """Client-facing view: remaining seconds instead of an epoch stamp (the
    client's clock is not the world clock and must not do this math)."""
    if now is None:
        now = world_clock.now()
    return [
        {"stat": e["stat"], "amount": e["amount"], "source": e["source"],
         "remaining_s": max(0, round(e["expires_at"] - now, 1))}
        for e in actor.active_effects
    ]
