"""Deterministic private-goal utility and intention selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class IntentionKind(StrEnum):
    KEEP_SCHEDULE = "keep_schedule"
    SATISFY_NEED = "satisfy_need"
    TRAVEL = "travel"
    SEEK_PERSON = "seek_person"
    AVOID_PERSON = "avoid_person"
    CONVERSE = "converse"
    WORK = "work"
    REST = "rest"
    HELP = "help"
    GUARD = "guard"
    REPORT = "report"
    FLEE = "flee"
    INVESTIGATE = "investigate"


@dataclass(frozen=True)
class GoalCandidate:
    key: str
    intention: IntentionKind
    base_priority: float
    target_id: str | None = None
    target_room_id: int | None = None
    need_pressure: float = 0
    conviction_pressure: float = 0
    relationship_pressure: float = 0
    deadline_urgency: float = 0
    opportunity: float = 0
    risk: float = 0
    travel_cost: float = 0
    commitment_cost: float = 0
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def utility(self) -> float:
        return (
            self.base_priority
            + self.need_pressure
            + self.conviction_pressure
            + self.relationship_pressure
            + self.deadline_urgency
            + self.opportunity
            - self.risk
            - self.travel_cost
            - self.commitment_cost
        )


@dataclass(frozen=True)
class Intention:
    goal_key: str
    kind: IntentionKind
    utility: float
    target_id: str | None = None
    target_room_id: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def choose_intention(
    candidates: list[GoalCandidate] | tuple[GoalCandidate, ...],
) -> Intention | None:
    """Choose the highest-utility valid candidate with stable tie-breaking."""
    if not candidates:
        return None
    winner = min(candidates, key=lambda goal: (-goal.utility, goal.key))
    return Intention(
        goal_key=winner.key,
        kind=winner.intention,
        utility=winner.utility,
        target_id=winner.target_id,
        target_room_id=winner.target_room_id,
        metadata=dict(winner.metadata),
    )


def need_candidate(
    *,
    need: str,
    value: float,
    threshold: float,
    destination_room_id: int | None,
) -> GoalCandidate | None:
    """Translate a low 0..100 need meter into an optional private goal."""
    if not 0 <= value <= 100:
        raise ValueError("need value must be within 0..100")
    if not 0 <= threshold <= 100:
        raise ValueError("need threshold must be within 0..100")
    if value >= threshold:
        return None
    pressure = (threshold - value) / max(1.0, threshold) * 100
    kind = IntentionKind.REST if need == "rest" else IntentionKind.SATISFY_NEED
    return GoalCandidate(
        key=f"need:{need}",
        intention=kind,
        base_priority=10,
        need_pressure=pressure,
        target_room_id=destination_room_id,
        metadata={"need": need},
    )
