"""Closed, data-only story-card predicates and effect proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


ALLOWED_PREDICATES = frozenset({
    "fact_equals",
    "fact_exists",
    "world_minute_at_least",
    "npc_alive",
    "npc_in_room",
    "relationship_at_least",
    "memory_tag_known",
    "event_occurred",
    "rumour_confidence_at_least",
})

ALLOWED_EFFECTS = frozenset({
    "add_memory",
    "modify_relationship",
    "add_goal",
    "cancel_goal",
    "reprioritize_goal",
    "begin_travel",
    "set_fact",
    "create_rumour",
    "schedule_event",
    "chronicle",
})


@dataclass(frozen=True)
class TriggerContext:
    world_minute: int
    facts: Mapping[str, object]
    npc_alive: Mapping[str, bool]
    npc_rooms: Mapping[str, int]
    relationships: Mapping[str, float]
    known_memory_tags: frozenset[tuple[str, str]]
    occurred_events: frozenset[str]
    rumour_confidence: Mapping[str, float]


def evaluate_condition(condition: Mapping[str, object], context: TriggerContext) -> bool:
    """Evaluate a closed predicate AST; arbitrary code is impossible."""
    if "all" in condition:
        children = condition["all"]
        if not isinstance(children, list):
            raise ValueError("all must contain a list")
        return all(evaluate_condition(child, context) for child in children)
    if "any" in condition:
        children = condition["any"]
        if not isinstance(children, list):
            raise ValueError("any must contain a list")
        return any(evaluate_condition(child, context) for child in children)
    if "not" in condition:
        child = condition["not"]
        if not isinstance(child, Mapping):
            raise ValueError("not must contain one condition")
        return not evaluate_condition(child, context)

    kind = condition.get("kind")
    if kind not in ALLOWED_PREDICATES:
        raise ValueError(f"unknown trigger predicate: {kind!r}")
    if kind == "fact_equals":
        return context.facts.get(str(condition.get("key"))) == condition.get("value")
    if kind == "fact_exists":
        return str(condition.get("key")) in context.facts
    if kind == "world_minute_at_least":
        return context.world_minute >= int(condition.get("value", 0))
    if kind == "npc_alive":
        return context.npc_alive.get(str(condition.get("npc_id"))) is bool(
            condition.get("value", True)
        )
    if kind == "npc_in_room":
        return context.npc_rooms.get(str(condition.get("npc_id"))) == int(
            condition.get("room_id", -1)
        )
    if kind == "relationship_at_least":
        key = (
            f"{condition.get('source_id')}:{condition.get('target_id')}:"
            f"{condition.get('axis')}"
        )
        return context.relationships.get(key, 0) >= float(condition.get("value", 0))
    if kind == "memory_tag_known":
        return (
            str(condition.get("npc_id")),
            str(condition.get("tag")),
        ) in context.known_memory_tags
    if kind == "event_occurred":
        return str(condition.get("event_key")) in context.occurred_events
    if kind == "rumour_confidence_at_least":
        return context.rumour_confidence.get(
            str(condition.get("rumour_id")), 0
        ) >= float(condition.get("value", 0))
    raise AssertionError("unreachable predicate")


def validate_effect(effect: Mapping[str, object]) -> None:
    kind = effect.get("kind")
    if kind not in ALLOWED_EFFECTS:
        raise ValueError(f"unknown story-card effect: {kind!r}")
