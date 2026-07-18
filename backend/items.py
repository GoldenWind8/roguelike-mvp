"""The item vocabulary: what an item IS and what its payload may SAY.

Design (docs/LOOT.md): an item is pure data in three layers —
  identity (name/description/rarity/art) + type + payload. The payload is a
  small CLOSED vocabulary of effect atoms; delivery systems (equip, consume,
  throw, weapon attack) interpret those atoms at use time. Data describes,
  systems interpret — the same contract pattern as rooms (room_validation)
  and personas (persona.validate_persona).

The effect atoms are deliberately GENERIC — none of them knows which item
type carries it:

  {"kind": "stat_mod", "stat": <STATS>, "amount": int, "duration_s": num?}
      A temporary or while-equipped change to a live stat. Wearables carry it
      WITHOUT duration_s (lasts while equipped); consumables/throwables carry
      it WITH duration_s (a world-clock timer, LOOT.md Decision 3).
  {"kind": "restore_hp", "amount": int}
      Instant heal, clamped to effective max_hp at apply time.
  {"kind": "restore_hunger", "amount": int}
      Instant food value, clamped to the hunger ceiling at apply time. A
      no-op on actors without a hunger meter (enemies) — the atom stays
      generic; the target decides whether it means anything.
  {"kind": "damage", "amount": int}
      Instant base damage — routed through the engine's Damage effect so
      defense math and death handling are never duplicated.

Validation raises ValueError with repair-grade messages (the room_validation
convention: the message is what you'd feed back to an LLM that emitted a bad
item). Caps are per-rarity DATA (RARITY_CAPS) — the same table validates
hand-authored seeds and clamps LLM output, so no path can mint a common item
with legendary numbers.
"""
from enum import Enum


class Rarity(str, Enum):
    COMMON = "common"
    RARE = "rare"
    LEGENDARY = "legendary"


class ItemType(str, Enum):
    WEARABLE = "wearable"
    CONSUMABLE = "consumable"
    THROWABLE = "throwable"
    WEAPON = "weapon"


# Live-stat names an atom may touch — exactly the Actor fields combat reads.
# Growing this list is deliberate: a new stat must exist on Actor first.
STATS = ("attack_damage", "defense", "max_hp")

# Art is a TYPED reference (LOOT.md Decision: swappable representation).
# "emoji" today; "url" arrives with image-gen — same column, no migration.
ART_KINDS = ("emoji", "url")

# Area shapes a throwable may resolve (closed, like TileType). "radius" is
# Manhattan distance — the same metric the old hard-coded bomb used.
AREA_SHAPES = ("radius",)

# Per-rarity ceilings — ONE table for seed validation and LLM clamping.
#   stat_total    max sum of |stat_mod amounts| across the payload
#   throw_damage  max damage atom amount on a throwable/consumable
#   heal          max restore_hp amount
#   hunger        max restore_hunger amount (food value; meter is 0-100)
#   weapon_damage max weapon damage
#   duration_s    max timer on any timed atom
RARITY_CAPS = {
    Rarity.COMMON: {"stat_total": 5, "throw_damage": 4, "heal": 25, "hunger": 30,
                    "weapon_damage": 35, "duration_s": 60},
    Rarity.RARE: {"stat_total": 12, "throw_damage": 8, "heal": 60, "hunger": 60,
                  "weapon_damage": 45, "duration_s": 180},
    Rarity.LEGENDARY: {"stat_total": 30, "throw_damage": 15, "heal": 100, "hunger": 100,
                       "weapon_damage": 70, "duration_s": 600},
}

# Geometry ceilings shared by every rarity (grid-scale, not power-scale).
MAX_RANGE = 6        # throw_range and weapon range
MAX_AREA_SIZE = 3

NAME_LIMIT = 40
DESCRIPTION_LIMIT = 200


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ValueError(message)


def validate_art(art) -> None:
    _require(isinstance(art, dict), f"art must be an object, got {art!r}")
    _require(art.get("kind") in ART_KINDS,
             f"art.kind must be one of {list(ART_KINDS)}, got {art.get('kind')!r}")
    value = art.get("value")
    _require(isinstance(value, str) and 0 < len(value) <= 8 if art.get("kind") == "emoji"
             else isinstance(value, str) and len(value) > 0,
             f"art.value must be a non-empty string (an emoji for kind 'emoji'), got {value!r}")


def _validate_atom(atom, *, timed: bool, caps: dict, what: str) -> None:
    """One effect atom. `timed` says whether stat_mod atoms here MUST carry a
    duration (consumable/throwable) or MUST NOT (wearable — lasts while
    equipped, a timer would fight the equip lifecycle)."""
    _require(isinstance(atom, dict), f"{what} must be an object, got {atom!r}")
    kind = atom.get("kind")

    if kind == "stat_mod":
        _require(atom.get("stat") in STATS,
                 f"{what}: stat must be one of {list(STATS)}, got {atom.get('stat')!r}")
        amount = atom.get("amount")
        _require(isinstance(amount, int) and amount != 0,
                 f"{what}: amount must be a non-zero int, got {amount!r}")
        if timed:
            duration = atom.get("duration_s")
            _require(isinstance(duration, (int, float)) and 0 < duration <= caps["duration_s"],
                     f"{what}: stat_mod needs duration_s in (0, {caps['duration_s']}] seconds")
        else:
            _require("duration_s" not in atom,
                     f"{what}: wearable stat_mod must not carry duration_s (it lasts while equipped)")
    elif kind == "restore_hp":
        amount = atom.get("amount")
        _require(isinstance(amount, int) and 0 < amount <= caps["heal"],
                 f"{what}: restore_hp amount must be an int in [1, {caps['heal']}]")
    elif kind == "restore_hunger":
        amount = atom.get("amount")
        _require(isinstance(amount, int) and 0 < amount <= caps["hunger"],
                 f"{what}: restore_hunger amount must be an int in [1, {caps['hunger']}]")
    elif kind == "damage":
        amount = atom.get("amount")
        _require(isinstance(amount, int) and 0 < amount <= caps["throw_damage"],
                 f"{what}: damage amount must be an int in [1, {caps['throw_damage']}]")
    else:
        raise ValueError(f"{what}: unknown effect kind {kind!r} — "
                         "valid: ['stat_mod', 'restore_hp', 'restore_hunger', 'damage']")


def _check_stat_budget(effects: list, caps: dict) -> None:
    total = sum(abs(a["amount"]) for a in effects if a.get("kind") == "stat_mod")
    _require(total <= caps["stat_total"],
             f"total |stat_mod| across the payload is {total}, "
             f"over this rarity's budget of {caps['stat_total']}")


def _validate_effects_list(payload: dict, *, timed: bool, allowed_kinds: set,
                           caps: dict) -> None:
    effects = payload.get("effects")
    _require(isinstance(effects, list) and effects,
             "payload needs a non-empty 'effects' list")
    for i, atom in enumerate(effects):
        _validate_atom(atom, timed=timed, caps=caps, what=f"effects[{i}]")
        _require(atom["kind"] in allowed_kinds,
                 f"effects[{i}]: kind '{atom['kind']}' is not allowed on this "
                 f"item type — allowed: {sorted(allowed_kinds)}")
    _check_stat_budget(effects, caps)


def validate_payload(item_type: ItemType, payload, rarity: Rarity) -> None:
    _require(isinstance(payload, dict), f"payload must be an object, got {payload!r}")
    caps = RARITY_CAPS[rarity]

    if item_type is ItemType.WEARABLE:
        # Passive while equipped: only un-timed stat mods make sense here —
        # instant atoms (heal/damage) have no "while worn" reading.
        _validate_effects_list(payload, timed=False, allowed_kinds={"stat_mod"}, caps=caps)

    elif item_type is ItemType.CONSUMABLE:
        _validate_effects_list(payload, timed=True,
                               allowed_kinds={"stat_mod", "restore_hp", "restore_hunger"},
                               caps=caps)

    elif item_type is ItemType.THROWABLE:
        rng = payload.get("throw_range")
        _require(isinstance(rng, int) and 1 <= rng <= MAX_RANGE,
                 f"throwable needs throw_range in [1, {MAX_RANGE}]")
        area = payload.get("area")
        _require(isinstance(area, dict) and area.get("shape") in AREA_SHAPES,
                 f"throwable needs area.shape in {list(AREA_SHAPES)}")
        size = area.get("size")
        _require(isinstance(size, int) and 0 <= size <= MAX_AREA_SIZE,
                 f"area.size must be in [0, {MAX_AREA_SIZE}] (0 = single tile)")
        _validate_effects_list(payload, timed=True,
                               allowed_kinds={"stat_mod", "restore_hp", "restore_hunger", "damage"},
                               caps=caps)

    elif item_type is ItemType.WEAPON:
        damage = payload.get("damage")
        _require(isinstance(damage, int) and 0 < damage <= caps["weapon_damage"],
                 f"weapon damage must be an int in [1, {caps['weapon_damage']}]")
        rng = payload.get("range")
        _require(isinstance(rng, int) and 1 <= rng <= MAX_RANGE,
                 f"weapon range must be in [1, {MAX_RANGE}] (1 = melee)")


def validate_item(data: dict) -> None:
    """Raise ValueError unless `data` is a storable item definition. The one
    gate every item passes — seeds at import, LLM output at generation."""
    _require(isinstance(data, dict), f"item must be an object, got {data!r}")

    name = data.get("name")
    _require(isinstance(name, str) and 0 < len(name.strip()) <= NAME_LIMIT,
             f"item needs a name of 1-{NAME_LIMIT} characters")
    description = data.get("description")
    _require(isinstance(description, str) and 0 < len(description.strip()) <= DESCRIPTION_LIMIT,
             f"item needs a description of 1-{DESCRIPTION_LIMIT} characters")

    try:
        rarity = Rarity(data.get("rarity"))
    except ValueError:
        raise ValueError(f"rarity must be one of {[r.value for r in Rarity]}, "
                         f"got {data.get('rarity')!r}")
    try:
        item_type = ItemType(data.get("type"))
    except ValueError:
        raise ValueError(f"type must be one of {[t.value for t in ItemType]}, "
                         f"got {data.get('type')!r}")

    validate_art(data.get("art"))
    validate_payload(item_type, data.get("payload"), rarity)


def stackable(item_type: str) -> bool:
    """Consumables and throwables stack; equipables (wearable/weapon) never do
    (LOOT.md inventory rules). Derived from type — never stored."""
    return item_type in (ItemType.CONSUMABLE.value, ItemType.THROWABLE.value)


def equipable(item_type: str) -> bool:
    return item_type in (ItemType.WEARABLE.value, ItemType.WEAPON.value)


def clamp_item(data: dict) -> dict:
    """Pull an (LLM-proposed) item's numbers down inside its rarity band
    instead of rejecting outright — forgiving on magnitude, strict on shape.
    Returns a new dict; validate_item still runs after this and owns every
    structural rejection."""
    if not isinstance(data, dict) or not isinstance(data.get("payload"), dict):
        return data
    try:
        caps = RARITY_CAPS[Rarity(data.get("rarity"))]
    except ValueError:
        return data

    clamped = {**data, "payload": {**data["payload"]}}
    payload = clamped["payload"]

    if isinstance(payload.get("damage"), int):
        payload["damage"] = min(payload["damage"], caps["weapon_damage"])
    if isinstance(payload.get("range"), int):
        payload["range"] = max(1, min(payload["range"], MAX_RANGE))
    if isinstance(payload.get("throw_range"), int):
        payload["throw_range"] = max(1, min(payload["throw_range"], MAX_RANGE))
    if isinstance(payload.get("area"), dict) and isinstance(payload["area"].get("size"), int):
        payload["area"] = {**payload["area"],
                           "size": max(0, min(payload["area"]["size"], MAX_AREA_SIZE))}

    if isinstance(payload.get("effects"), list):
        effects = []
        budget = caps["stat_total"]
        for atom in payload["effects"]:
            if not isinstance(atom, dict):
                effects.append(atom)
                continue
            atom = dict(atom)
            amount = atom.get("amount")
            if atom.get("kind") == "restore_hp" and isinstance(amount, int):
                atom["amount"] = min(amount, caps["heal"])
            elif atom.get("kind") == "restore_hunger" and isinstance(amount, int):
                atom["amount"] = min(amount, caps["hunger"])
            elif atom.get("kind") == "damage" and isinstance(amount, int):
                atom["amount"] = min(amount, caps["throw_damage"])
            elif atom.get("kind") == "stat_mod" and isinstance(amount, int) and amount != 0:
                sign = 1 if amount > 0 else -1
                magnitude = min(abs(amount), budget)
                if magnitude == 0:
                    continue  # budget exhausted — drop the atom entirely
                budget -= magnitude
                atom["amount"] = sign * magnitude
            if (atom.get("kind") == "stat_mod"
                    and isinstance(atom.get("duration_s"), (int, float))):
                atom["duration_s"] = min(atom["duration_s"], caps["duration_s"])
            effects.append(atom)
        payload["effects"] = effects

    return clamped


def item_view(row) -> dict:
    """ItemDef row -> the plain dict that travels in inventories, chest
    contents, and client messages. Denormalized ON PURPOSE: a held item is a
    snapshot, self-contained, so the hot loop and the client never need the
    items table (LOOT.md Decision: DB at the edges)."""
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "rarity": row.rarity,
        "type": row.item_type,
        "art": row.art,
        "payload": row.payload,
        "origin": row.origin,
    }
