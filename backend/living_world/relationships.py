"""Directional multi-axis social state."""

from __future__ import annotations

from dataclasses import dataclass, replace

RELATIONSHIP_MIN = -100
RELATIONSHIP_MAX = 100
FAMILIARITY_MIN = 0
FAMILIARITY_MAX = 100


def _bounded(value: float, *, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Relationship:
    affinity: float = 0
    trust: float = 0
    fear: float = 0
    respect: float = 0
    obligation: float = 0
    intimacy: float = 0
    grievance: float = 0
    familiarity: float = 0


def apply_relationship_delta(
    relationship: Relationship,
    **deltas: float,
) -> Relationship:
    unknown = set(deltas) - set(Relationship.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown relationship axes: {sorted(unknown)}")
    changes: dict[str, float] = {}
    for axis, delta in deltas.items():
        current = getattr(relationship, axis)
        if axis == "familiarity":
            changes[axis] = _bounded(
                current + delta,
                low=FAMILIARITY_MIN,
                high=FAMILIARITY_MAX,
            )
        else:
            changes[axis] = _bounded(
                current + delta,
                low=RELATIONSHIP_MIN,
                high=RELATIONSHIP_MAX,
            )
    return replace(relationship, **changes)


def bond_word(relationship: Relationship) -> str:
    """Player-facing descriptor; raw social numbers stay private."""
    if relationship.grievance >= 70 or (
        relationship.affinity <= -60 and relationship.trust <= -40
    ):
        return "hostile"
    if relationship.fear >= 60 or relationship.trust <= -25:
        return "wary"
    if relationship.familiarity < 15:
        return "unfamiliar"
    if relationship.trust >= 70 and relationship.affinity >= 65:
        return "devoted"
    if relationship.trust >= 40 and relationship.affinity >= 25:
        return "trusting"
    return "cordial"
