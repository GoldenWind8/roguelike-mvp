"""Delivery seam: item payload ATOMS -> trusted engine EFFECTS.

The one translation table between the item vocabulary (items.py — untrusted
data, LLM-writable, validated at the pool gate) and the engine's effect union
(effects.py — trusted, the only things allowed to mutate a room). Consume and
throw both funnel through here, so an atom behaves identically however it is
delivered — and a new atom kind is exactly two edits: its validation in
items.py, its translation here.
"""
from backend.effects import Damage, Effect, Heal, RestoreHunger, TimedStat


def atom_effects(target_id: str, atoms: list[dict], *, source_id: str | None,
                 source_name: str) -> list[Effect]:
    """The engine effects that land `atoms` on one target. Unknown kinds are
    impossible by construction (the pool gate rejects them), so this raises
    rather than skips — a KeyError here means an unvalidated item got in."""
    translated: list[Effect] = []
    for atom in atoms:
        kind = atom["kind"]
        if kind == "restore_hp":
            translated.append(Heal(target_id, atom["amount"], source_id=source_id))
        elif kind == "restore_hunger":
            translated.append(RestoreHunger(target_id, atom["amount"], source_id=source_id))
        elif kind == "damage":
            translated.append(Damage(target_id, atom["amount"], source_id=source_id))
        elif kind == "stat_mod":
            translated.append(TimedStat(
                target_id, atom["stat"], atom["amount"], atom["duration_s"],
                source=source_name, source_id=source_id,
            ))
        else:
            raise KeyError(f"unvalidated atom kind {kind!r} reached delivery")
    return translated
