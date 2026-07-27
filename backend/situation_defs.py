"""Closed, authored definitions for consequential world interactions.

Situations are not quests.  They have no accepted state, objective list, or
completion UI.  A placed object can expose one brief decision when the player
has personally found enough evidence; resolving it writes one exclusive world
fact and lets the ordinary living-world trigger system carry consequences.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.content import load_catalog


_DISPOSITIONS = frozenset({"hostile", "neutral", "friendly"})
_GOAL_STATUSES = frozenset({
    "dormant",
    "active",
    "blocked",
    "completed",
    "failed",
    "abandoned",
})


def _text(value: object, path: str, *, maximum: int = 600) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{path} must be non-empty text")
    result = " ".join(value.split())
    if len(result) > maximum:
        raise RuntimeError(f"{path} exceeds {maximum} characters")
    return result


def _identifier(value: object, path: str) -> str:
    result = _text(value, path, maximum=100)
    if any(not (char.islower() or char.isdigit() or char in "-_:") for char in result):
        raise RuntimeError(f"{path} is not a safe identifier")
    return result


def _fact_key(value: object, path: str) -> str:
    result = _text(value, path, maximum=100)
    if any(
        not (char.islower() or char.isdigit() or char in ".-_")
        for char in result
    ):
        raise RuntimeError(f"{path} is not a safe world-fact key")
    return result


def _closed(entry: object, path: str, fields: set[str]) -> dict:
    if not isinstance(entry, dict):
        raise RuntimeError(f"{path} must be an object")
    unknown = set(entry) - fields
    missing = fields - set(entry)
    if unknown:
        raise RuntimeError(f"{path} has unknown fields: {sorted(unknown)}")
    if missing:
        raise RuntimeError(f"{path} is missing fields: {sorted(missing)}")
    return entry


@dataclass(frozen=True)
class SituationDefeat:
    value: str
    fact_value: dict[str, object]
    result: str
    chronicle: str


@dataclass(frozen=True)
class SituationTerminalOutcome:
    """An authored world fact that permanently closes player choices.

    Some situations can end through the ordinary off-screen simulation rather
    than through a player choice or the actor's defeat. The terminal fact is
    matched exactly so a later inspection or forged resolution request reads
    that durable ending instead of reopening an already-lost moment.
    """

    fact_key: str
    value: dict[str, object]
    outcome: str
    result: str


@dataclass(frozen=True)
class SituationChoice:
    id: str
    label: str
    description: str
    requires_all_clues: tuple[str, ...]
    outcome: str
    fact_value: dict[str, object]
    actor_disposition: str
    actor_goal_id: str
    actor_goal_status: str
    result: str
    chronicle: str


@dataclass(frozen=True)
class SituationDefinition:
    id: str
    object_id: str
    actor_id: str
    title: str
    kicker: str
    description: str
    fact_key: str
    defeat_outcome: SituationDefeat
    terminal_outcomes: tuple[SituationTerminalOutcome, ...]
    choices: tuple[SituationChoice, ...]


def _json_value(value: object, path: str) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        if any(not isinstance(key, str) or not key for key in value):
            raise RuntimeError(f"{path} keys must be non-empty text")
        return {
            key: _json_value(item, f"{path}.{key}")
            for key, item in value.items()
        }
    raise RuntimeError(f"{path} must contain only JSON values")


def _fact_value(value: object, path: str, *, expected_state: str) -> dict[str, object]:
    parsed = _json_value(value, path)
    if not isinstance(parsed, dict) or not parsed:
        raise RuntimeError(f"{path} must be a non-empty object")
    if parsed.get("state") != expected_state:
        raise RuntimeError(f"{path}.state must equal {expected_state!r}")
    return parsed


def _defeat(value: object, path: str) -> SituationDefeat:
    item = _closed(value, path, {"value", "fact_value", "result", "chronicle"})
    outcome = _identifier(item["value"], f"{path}.value")
    return SituationDefeat(
        value=outcome,
        fact_value=_fact_value(
            item["fact_value"],
            f"{path}.fact_value",
            expected_state=outcome,
        ),
        result=_text(item["result"], f"{path}.result"),
        chronicle=_text(item["chronicle"], f"{path}.chronicle"),
    )


def _terminal_outcome(
    value: object,
    path: str,
) -> SituationTerminalOutcome:
    item = _closed(value, path, {
        "fact_key",
        "value",
        "outcome",
        "result",
    })
    outcome = _identifier(item["outcome"], f"{path}.outcome")
    return SituationTerminalOutcome(
        fact_key=_fact_key(item["fact_key"], f"{path}.fact_key"),
        value=_fact_value(
            item["value"],
            f"{path}.value",
            expected_state=outcome,
        ),
        outcome=outcome,
        result=_text(item["result"], f"{path}.result"),
    )


def _choice(value: object, path: str) -> SituationChoice:
    item = _closed(value, path, {
        "id",
        "label",
        "description",
        "requires_all_clues",
        "outcome",
        "fact_value",
        "actor_disposition",
        "actor_goal_id",
        "actor_goal_status",
        "result",
        "chronicle",
    })
    clues = item["requires_all_clues"]
    if (
        not isinstance(clues, list)
        or not clues
        or len(clues) != len(set(clues))
    ):
        raise RuntimeError(
            f"{path}.requires_all_clues must be a non-empty unique list"
        )
    disposition = item["actor_disposition"]
    if disposition not in _DISPOSITIONS:
        raise RuntimeError(
            f"{path}.actor_disposition must be one of {sorted(_DISPOSITIONS)}"
        )
    status = item["actor_goal_status"]
    if status not in _GOAL_STATUSES:
        raise RuntimeError(
            f"{path}.actor_goal_status must be one of {sorted(_GOAL_STATUSES)}"
        )
    outcome = _identifier(item["outcome"], f"{path}.outcome")
    return SituationChoice(
        id=_identifier(item["id"], f"{path}.id"),
        label=_text(item["label"], f"{path}.label", maximum=80),
        description=_text(
            item["description"],
            f"{path}.description",
            maximum=240,
        ),
        requires_all_clues=tuple(
            _identifier(clue, f"{path}.requires_all_clues[{index}]")
            for index, clue in enumerate(clues)
        ),
        outcome=outcome,
        fact_value=_fact_value(
            item["fact_value"],
            f"{path}.fact_value",
            expected_state=outcome,
        ),
        actor_disposition=disposition,
        actor_goal_id=_identifier(
            item["actor_goal_id"],
            f"{path}.actor_goal_id",
        ),
        actor_goal_status=status,
        result=_text(item["result"], f"{path}.result"),
        chronicle=_text(item["chronicle"], f"{path}.chronicle"),
    )


def _definition(entry: dict) -> SituationDefinition:
    situation_id = entry.get("id", "<unknown>")
    path = f"situation {situation_id!r}"
    item = _closed(entry, path, {
        "id",
        "object_id",
        "actor_id",
        "title",
        "kicker",
        "description",
        "fact_key",
        "defeat_outcome",
        "terminal_outcomes",
        "choices",
    })
    raw_terminals = item["terminal_outcomes"]
    if not isinstance(raw_terminals, list):
        raise RuntimeError(f"{path}.terminal_outcomes must be a list")
    terminal_outcomes = tuple(
        _terminal_outcome(
            terminal,
            f"{path}.terminal_outcomes[{index}]",
        )
        for index, terminal in enumerate(raw_terminals)
    )
    terminal_fact_keys = [terminal.fact_key for terminal in terminal_outcomes]
    terminal_states = [terminal.outcome for terminal in terminal_outcomes]
    if len(terminal_fact_keys) != len(set(terminal_fact_keys)):
        raise RuntimeError(f"{path}.terminal_outcomes repeats a fact_key")
    if len(terminal_states) != len(set(terminal_states)):
        raise RuntimeError(f"{path}.terminal_outcomes repeats an outcome")
    raw_choices = item["choices"]
    if not isinstance(raw_choices, list) or len(raw_choices) < 2:
        raise RuntimeError(f"{path}.choices must contain at least two choices")
    choices = tuple(
        _choice(choice, f"{path}.choices[{index}]")
        for index, choice in enumerate(raw_choices)
    )
    choice_ids = [choice.id for choice in choices]
    outcomes = [choice.outcome for choice in choices]
    if len(choice_ids) != len(set(choice_ids)):
        raise RuntimeError(f"{path}.choices repeats an id")
    if len(outcomes) != len(set(outcomes)):
        raise RuntimeError(f"{path}.choices repeats an outcome")
    defeat = _defeat(item["defeat_outcome"], f"{path}.defeat_outcome")
    if defeat.value in outcomes:
        raise RuntimeError(f"{path}.defeat_outcome duplicates a choice outcome")
    if set(terminal_states) & ({defeat.value} | set(outcomes)):
        raise RuntimeError(
            f"{path}.terminal_outcomes duplicates a choice or defeat outcome"
        )
    return SituationDefinition(
        id=_identifier(item["id"], f"{path}.id"),
        object_id=_identifier(item["object_id"], f"{path}.object_id"),
        actor_id=_identifier(item["actor_id"], f"{path}.actor_id"),
        title=_text(item["title"], f"{path}.title", maximum=100),
        kicker=_text(item["kicker"], f"{path}.kicker", maximum=80),
        description=_text(item["description"], f"{path}.description"),
        fact_key=_fact_key(item["fact_key"], f"{path}.fact_key"),
        defeat_outcome=defeat,
        terminal_outcomes=terminal_outcomes,
        choices=choices,
    )


_BY_ID = {
    situation_id: _definition(entry)
    for situation_id, entry in load_catalog("situations.json").items()
}
_BY_OBJECT = {definition.object_id: definition for definition in _BY_ID.values()}
_BY_ACTOR = {definition.actor_id: definition for definition in _BY_ID.values()}
_BY_FACT = {definition.fact_key: definition for definition in _BY_ID.values()}
if len(_BY_OBJECT) != len(_BY_ID):
    raise RuntimeError("situations.json repeats an object_id")
if len(_BY_ACTOR) != len(_BY_ID):
    raise RuntimeError("situations.json repeats an actor_id")
if len(_BY_FACT) != len(_BY_ID):
    raise RuntimeError("situations.json repeats a fact_key")


def get_situation(situation_id: str) -> SituationDefinition | None:
    return _BY_ID.get(situation_id)


def get_situation_for_object(object_id: str) -> SituationDefinition | None:
    return _BY_OBJECT.get(object_id)


def get_situation_for_actor(actor_id: str) -> SituationDefinition | None:
    return _BY_ACTOR.get(actor_id)
