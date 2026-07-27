"""Loader and strict validators for authored living-world content.

The files in ``content/living_world`` describe people, beliefs, roads, and
causal encounters.  They deliberately do not describe quests.  NPC planning
may choose a *direction* from this material, but movement and state mutation
remain the responsibility of deterministic runtime systems.

The validators are intentionally closed: every object has an exact property
set and every executable-looking value comes from a small vocabulary.  This
keeps authored JSON safe to feed into both deterministic simulation and
optional language-model context.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content"
LIVING_WORLD_ROOT = CONTENT_ROOT / "living_world"

_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_FORBIDDEN_KEYS = {
    "objective",
    "objectives",
    "quest",
    "quests",
    "quest_id",
    "quest_state",
    "status",
}

DAY_NAMES = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}
DAY_PHASES = {"dawn", "day", "dusk", "night"}
ROUTE_RISKS = {"safe", "guarded", "dangerous", "dire"}
ROUTE_RISK_RANK = {"safe": 0, "guarded": 1, "dangerous": 2, "dire": 3}

LOCATION_KINDS = {
    "capital",
    "city_district",
    "town",
    "village",
    "waystation",
    "border_post",
    "field",
    "road_site",
    "hospice",
}
LOCATION_TAGS = {
    "archive",
    "carriage_stop",
    "docks",
    "healer",
    "lodging",
    "market",
    "rot_signs",
    "safe_at_night",
    "stables",
    "work",
    "worship",
}
PASSAGE_TYPES = {
    "causeway",
    "city_road",
    "field_road",
    "forest_track",
    "hedgerow_track",
    "marsh_road",
    "mountain_pass",
}
FRONTIER_BIOMES = {
    "amberfall_fields",
    "deep_frontier",
    "drazna_marches",
    "rouvray_lowlands",
}
FRONTIER_ARCHETYPES = {
    "black_marsh",
    "braided_river",
    "caravan_remains",
    "grave_moor",
    "old_battlefield",
    "pilgrim_road",
    "ravine_crossing",
    "rotwood",
}
THREAT_KINDS = {
    "barrow_dead",
    "black_silt",
    "deserters",
    "drowned_dead",
    "feral_hounds",
    "floodwater",
    "rockfall",
    "road_bandits",
    "rot_growth",
    "sinkholes",
    "wolves",
    "zealots",
}
BYPASS_KINDS = {
    "guarded_convoy",
    "local_guide",
    "low_water",
    "none",
    "paid_toll",
    "weather_window",
}

NPC_KINDS = {"official", "resident", "traveller", "worker"}
DELIBERATION_PURPOSES = {"care", "replan", "safety", "social", "travel", "work"}
SCHEDULE_ACTIVITIES = {
    "care",
    "eat",
    "hide",
    "investigate",
    "patrol",
    "repair",
    "sleep",
    "socialize",
    "travel",
    "work",
    "worship",
}
NEED_KINDS = {
    "answers",
    "belonging",
    "coin",
    "duty",
    "food",
    "grief",
    "health",
    "rest",
    "safety",
}
SATISFIER_ACTIONS = {
    "care",
    "create",
    "earn_coin",
    "eat",
    "grieve",
    "hide",
    "investigate",
    "pray",
    "protect",
    "repair",
    "seek_care",
    "seek_safety",
    "sleep",
    "socialize",
    "travel",
    "work",
}
GOAL_APPROACHES = {
    "direct",
    "dutiful",
    "opportunistic",
    "patient",
    "protective",
    "secretive",
    "social",
}
GOAL_TARGET_KINDS = {"kingdom", "location", "npc", "rumor", "self"}
RISK_TOLERANCES = {"avoidant", "bold", "measured"}
TRAVEL_MODES = {"carriage", "ferry", "walk"}

RUMOR_TRUTH = {"confirmed", "false", "partial", "unresolved"}
TRUTH_ALIGNMENT = {"accurate", "false", "partial", "unresolved"}
SOURCE_KINDS = {"anonymous", "document", "firsthand", "npc", "official_notice", "place"}
DISTORTION_KINDS = {"soft", "stable", "volatile"}
RUMOR_CONTEXTS = {"camp", "carriage", "inn", "market", "road", "work"}

TRIGGER_KINDS = {"conversation", "story"}
CONVERSATION_MODES = {"continuous", "single_exchange"}
CONVERSATION_TOPICS = {
    "black_rot",
    "carriages",
    "family",
    "flood",
    "missing_people",
    "medicine",
    "roads",
    "rumors",
}
CONVERSATION_STOPS = {"danger", "separated", "speaker_refuses", "topic_exhausted"}
RELATIONSHIP_AXES = {"affinity", "fear", "obligation", "respect", "trust"}
MEMORY_TAGS = {
    "carriage",
    "danger",
    "departure",
    "family",
    "grief",
    "health",
    "injury",
    "person",
    "promise",
    "road",
    "rot",
    "testimony",
    "warning",
    "death",
}
GOAL_STATUSES = {
    "dormant",
    "active",
    "blocked",
    "completed",
    "failed",
    "abandoned",
}
NPC_DISPOSITIONS = {"hostile", "neutral", "friendly"}


class LivingWorldContentError(RuntimeError):
    """Raised when authored living-world content violates its closed schema."""


@dataclass(frozen=True, slots=True)
class LivingWorldContent:
    """Validated living-world documents and convenient stable-id indexes."""

    world_document: dict[str, Any]
    npc_document: dict[str, Any]
    rumor_document: dict[str, Any]
    trigger_document: dict[str, Any]
    carriage_network: Mapping[str, Any]
    kingdoms: Mapping[str, dict[str, Any]]
    locations: Mapping[str, dict[str, Any]]
    routes: Mapping[str, dict[str, Any]]
    hostile_passages: Mapping[str, dict[str, Any]]
    carriages: Mapping[str, dict[str, Any]]
    npc_profiles: Mapping[str, dict[str, Any]]
    rumors: Mapping[str, dict[str, Any]]
    triggers: Mapping[str, dict[str, Any]]


def _fail(path: str, message: str) -> None:
    raise LivingWorldContentError(f"{path}: {message}")


def _closed_object(
    value: Any,
    path: str,
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    required_set = set(required)
    allowed = required_set | set(optional)
    unknown = set(value) - allowed
    missing = required_set - set(value)
    if unknown:
        _fail(path, f"unknown properties: {', '.join(sorted(unknown))}")
    if missing:
        _fail(path, f"missing properties: {', '.join(sorted(missing))}")
    return value


def _array(
    value: Any,
    path: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    if len(value) < minimum:
        _fail(path, f"must contain at least {minimum} entries")
    if maximum is not None and len(value) > maximum:
        _fail(path, f"must contain at most {maximum} entries")
    return value


def _string(value: Any, path: str, *, non_empty: bool = True) -> str:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if non_empty and not value.strip():
        _fail(path, "must not be empty")
    return value


def _identifier(value: Any, path: str) -> str:
    identifier = _string(value, path)
    if not _ID_PATTERN.fullmatch(identifier):
        _fail(path, "must be a lowercase stable id")
    return identifier


def _integer(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "must be an integer")
    if minimum is not None and value < minimum:
        _fail(path, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        _fail(path, f"must be at most {maximum}")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, "must be a boolean")
    return value


def _enum(value: Any, path: str, choices: set[str]) -> str:
    result = _string(value, path)
    if result not in choices:
        _fail(path, f"must be one of: {', '.join(sorted(choices))}")
    return result


def _string_array(
    value: Any,
    path: str,
    *,
    choices: set[str] | None = None,
    minimum: int = 0,
    maximum: int | None = None,
    identifiers: bool = False,
) -> list[str]:
    raw = _array(value, path, minimum=minimum, maximum=maximum)
    result: list[str] = []
    for index, entry in enumerate(raw):
        item_path = f"{path}[{index}]"
        item = _identifier(entry, item_path) if identifiers else _string(entry, item_path)
        if choices is not None and item not in choices:
            _fail(item_path, f"must be one of: {', '.join(sorted(choices))}")
        result.append(item)
    if len(result) != len(set(result)):
        _fail(path, "must not contain duplicates")
    return result


def _index(entries: list[dict[str, Any]], path: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        identifier = _identifier(entry.get("id"), f"{path}[{index}].id")
        if identifier in result:
            _fail(path, f"duplicate id {identifier!r}")
        result[identifier] = entry
    return result


def _reject_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            # ``set_goal_status`` changes an NPC's private simulation goal; it
            # is not player quest state. Keep the broad anti-quest guard while
            # allowing that one closed-schema effect field.
            private_goal_status = (
                normalized == "status"
                and value.get("kind") == "set_goal_status"
            )
            if normalized in _FORBIDDEN_KEYS and not private_goal_status:
                _fail(f"{path}.{key}", "tracked quest/objective state is not allowed")
            _reject_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def _validate_schema_root(document: Any, path: str, collection: str) -> dict[str, Any]:
    root = _closed_object(
        document,
        path,
        required={"schema_version", collection},
    )
    if root["schema_version"] != 1:
        _fail(f"{path}.schema_version", "must equal 1")
    _array(root[collection], f"{path}.{collection}", minimum=1)
    return root


def _validate_world(
    document: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    root = _closed_object(
        document,
        "world.json",
        required={
            "schema_version",
            "world",
            "carriage_network",
            "kingdoms",
            "locations",
            "routes",
            "hostile_passages",
            "carriages",
        },
    )
    if root["schema_version"] != 1:
        _fail("world.json.schema_version", "must equal 1")

    world = _closed_object(
        root["world"],
        "world.json.world",
        required={"id", "name", "day_length_minutes"},
    )
    _identifier(world["id"], "world.json.world.id")
    _string(world["name"], "world.json.world.name")
    if _integer(
        world["day_length_minutes"],
        "world.json.world.day_length_minutes",
        minimum=1,
    ) != 1440:
        _fail("world.json.world.day_length_minutes", "must equal 1440")

    carriage_network = _closed_object(
        root["carriage_network"],
        "world.json.carriage_network",
        required={"scope", "network_visibility", "generated_frontier", "fast_travel"},
    )
    if _string(carriage_network["scope"], "world.json.carriage_network.scope") != "shared_world":
        _fail("world.json.carriage_network.scope", "must equal 'shared_world'")
    if (
        _string(
            carriage_network["network_visibility"],
            "world.json.carriage_network.network_visibility",
        )
        != "public"
    ):
        _fail(
            "world.json.carriage_network.network_visibility",
            "must equal 'public'",
        )
    generated_frontier = _closed_object(
        carriage_network["generated_frontier"],
        "world.json.carriage_network.generated_frontier",
        required={
            "eligible_biomes",
            "minimum_depth",
            "guaranteed_archetypes",
            "other_room_chance_percent",
            "requires_road_connection",
            "requires_arrival_before_naming",
            "first_arrival_may_name",
            "default_stop_name",
            "name_character_limit",
            "generated_departure_minutes",
        },
    )
    _string_array(
        generated_frontier["eligible_biomes"],
        "world.json.carriage_network.generated_frontier.eligible_biomes",
        choices=FRONTIER_BIOMES,
        minimum=1,
    )
    _integer(
        generated_frontier["minimum_depth"],
        "world.json.carriage_network.generated_frontier.minimum_depth",
        minimum=0,
    )
    _string_array(
        generated_frontier["guaranteed_archetypes"],
        "world.json.carriage_network.generated_frontier.guaranteed_archetypes",
        choices=FRONTIER_ARCHETYPES,
        minimum=1,
    )
    _integer(
        generated_frontier["other_room_chance_percent"],
        "world.json.carriage_network.generated_frontier.other_room_chance_percent",
        minimum=0,
        maximum=100,
    )
    for field in (
        "requires_road_connection",
        "requires_arrival_before_naming",
        "first_arrival_may_name",
    ):
        if not _boolean(
            generated_frontier[field],
            f"world.json.carriage_network.generated_frontier.{field}",
        ):
            _fail(
                f"world.json.carriage_network.generated_frontier.{field}",
                "must be true for community frontier stops",
            )
    default_stop_name = _string(
        generated_frontier["default_stop_name"],
        "world.json.carriage_network.generated_frontier.default_stop_name",
    )
    if default_stop_name != "Unnamed Waystop":
        _fail(
            "world.json.carriage_network.generated_frontier.default_stop_name",
            "must equal 'Unnamed Waystop'",
        )
    if (
        _integer(
            generated_frontier["name_character_limit"],
            "world.json.carriage_network.generated_frontier.name_character_limit",
            minimum=2,
            maximum=64,
        )
        != 32
    ):
        _fail(
            "world.json.carriage_network.generated_frontier.name_character_limit",
            "must equal the shared service limit of 32",
        )
    generated_departures = _array(
        generated_frontier["generated_departure_minutes"],
        "world.json.carriage_network.generated_frontier.generated_departure_minutes",
        minimum=1,
        maximum=6,
    )
    departure_minutes = [
        _integer(
            minute,
            f"world.json.carriage_network.generated_frontier.generated_departure_minutes[{index}]",
            minimum=0,
            maximum=1439,
        )
        for index, minute in enumerate(generated_departures)
    ]
    if departure_minutes != sorted(set(departure_minutes)):
        _fail(
            "world.json.carriage_network.generated_frontier.generated_departure_minutes",
            "minutes must be unique and in ascending order",
        )
    fast_travel = _closed_object(
        carriage_network["fast_travel"],
        "world.json.carriage_network.fast_travel",
        required={
            "uses_route_travel_minutes",
            "requires_operating_window",
            "advances_world_time",
            "moves_party_together",
            "resolves_route_pressure",
        },
    )
    for field in (
        "uses_route_travel_minutes",
        "requires_operating_window",
        "advances_world_time",
        "moves_party_together",
        "resolves_route_pressure",
    ):
        if not _boolean(
            fast_travel[field],
            f"world.json.carriage_network.fast_travel.{field}",
        ):
            _fail(
                f"world.json.carriage_network.fast_travel.{field}",
                "must be true",
            )

    kingdom_entries = _array(root["kingdoms"], "world.json.kingdoms", minimum=2, maximum=3)
    for index, kingdom in enumerate(kingdom_entries):
        path = f"world.json.kingdoms[{index}]"
        item = _closed_object(
            kingdom,
            path,
            required={
                "id",
                "name",
                "demonym",
                "capital_location_id",
                "first_public_rot_record",
                "rot_proximity",
                "public_account",
                "travel_motive",
                "character",
            },
        )
        _identifier(item["id"], f"{path}.id")
        for field in ("name", "demonym", "public_account", "travel_motive", "character"):
            _string(item[field], f"{path}.{field}")
        _identifier(item["capital_location_id"], f"{path}.capital_location_id")
        _boolean(item["first_public_rot_record"], f"{path}.first_public_rot_record")
        _enum(
            item["rot_proximity"],
            f"{path}.rot_proximity",
            {"disputed", "endemic", "hearsay", "witnessed"},
        )
    kingdoms = _index(kingdom_entries, "world.json.kingdoms")
    first_records = [
        kingdom_id
        for kingdom_id, kingdom in kingdoms.items()
        if kingdom["first_public_rot_record"]
    ]
    if len(first_records) != 1:
        _fail(
            "world.json.kingdoms",
            "exactly one kingdom must hold the first public black-rot record",
        )

    location_entries = _array(root["locations"], "world.json.locations", minimum=3)
    for index, location in enumerate(location_entries):
        path = f"world.json.locations[{index}]"
        item = _closed_object(
            location,
            path,
            required={"id", "name", "kingdom_id", "kind", "description", "tags"},
        )
        _identifier(item["id"], f"{path}.id")
        _string(item["name"], f"{path}.name")
        kingdom_id = _identifier(item["kingdom_id"], f"{path}.kingdom_id")
        if kingdom_id not in kingdoms:
            _fail(f"{path}.kingdom_id", f"unknown kingdom {kingdom_id!r}")
        _enum(item["kind"], f"{path}.kind", LOCATION_KINDS)
        _string(item["description"], f"{path}.description")
        _string_array(item["tags"], f"{path}.tags", choices=LOCATION_TAGS)
    locations = _index(location_entries, "world.json.locations")

    for kingdom_id, kingdom in kingdoms.items():
        capital_id = kingdom["capital_location_id"]
        capital = locations.get(capital_id)
        if capital is None:
            _fail(
                f"world.json.kingdoms[{kingdom_id}].capital_location_id",
                f"unknown location {capital_id!r}",
            )
        if capital["kingdom_id"] != kingdom_id or capital["kind"] != "capital":
            _fail(
                f"world.json.kingdoms[{kingdom_id}].capital_location_id",
                "must reference a capital in the same kingdom",
            )

    route_entries = _array(root["routes"], "world.json.routes", minimum=1)
    for index, route in enumerate(route_entries):
        path = f"world.json.routes[{index}]"
        item = _closed_object(
            route,
            path,
            required={
                "id",
                "name",
                "from_location_id",
                "to_location_id",
                "passage",
                "travel_minutes",
                "risk",
                "hostile_passage_ids",
                "description",
            },
        )
        _identifier(item["id"], f"{path}.id")
        _string(item["name"], f"{path}.name")
        source = _identifier(item["from_location_id"], f"{path}.from_location_id")
        target = _identifier(item["to_location_id"], f"{path}.to_location_id")
        if source == target:
            _fail(path, "route endpoints must differ")
        for field, location_id in (("from_location_id", source), ("to_location_id", target)):
            if location_id not in locations:
                _fail(f"{path}.{field}", f"unknown location {location_id!r}")
        _enum(item["passage"], f"{path}.passage", PASSAGE_TYPES)
        _integer(item["travel_minutes"], f"{path}.travel_minutes", minimum=1)
        _enum(item["risk"], f"{path}.risk", ROUTE_RISKS)
        _string_array(
            item["hostile_passage_ids"],
            f"{path}.hostile_passage_ids",
            identifiers=True,
        )
        _string(item["description"], f"{path}.description")
    routes = _index(route_entries, "world.json.routes")

    passage_entries = _array(
        root["hostile_passages"],
        "world.json.hostile_passages",
        minimum=1,
    )
    for index, passage in enumerate(passage_entries):
        path = f"world.json.hostile_passages[{index}]"
        item = _closed_object(
            passage,
            path,
            required={
                "id",
                "route_id",
                "name",
                "segment_start_percent",
                "segment_end_percent",
                "threats",
                "active_phases",
                "bypass",
                "encounter_pressure",
                "warning",
                "description",
            },
        )
        _identifier(item["id"], f"{path}.id")
        route_id = _identifier(item["route_id"], f"{path}.route_id")
        if route_id not in routes:
            _fail(f"{path}.route_id", f"unknown route {route_id!r}")
        _string(item["name"], f"{path}.name")
        start = _integer(
            item["segment_start_percent"],
            f"{path}.segment_start_percent",
            minimum=0,
            maximum=99,
        )
        end = _integer(
            item["segment_end_percent"],
            f"{path}.segment_end_percent",
            minimum=1,
            maximum=100,
        )
        if start >= end:
            _fail(path, "segment_start_percent must precede segment_end_percent")
        _string_array(
            item["threats"],
            f"{path}.threats",
            choices=THREAT_KINDS,
            minimum=1,
        )
        _string_array(
            item["active_phases"],
            f"{path}.active_phases",
            choices=DAY_PHASES,
            minimum=1,
        )
        _enum(item["bypass"], f"{path}.bypass", BYPASS_KINDS)
        _integer(
            item["encounter_pressure"],
            f"{path}.encounter_pressure",
            minimum=1,
            maximum=100,
        )
        _string(item["warning"], f"{path}.warning")
        _string(item["description"], f"{path}.description")
    hostile_passages = _index(passage_entries, "world.json.hostile_passages")

    referenced_passages: set[str] = set()
    for route_id, route in routes.items():
        for passage_id in route["hostile_passage_ids"]:
            passage = hostile_passages.get(passage_id)
            if passage is None:
                _fail(
                    f"world.json.routes[{route_id}].hostile_passage_ids",
                    f"unknown hostile passage {passage_id!r}",
                )
            if passage["route_id"] != route_id:
                _fail(
                    f"world.json.routes[{route_id}].hostile_passage_ids",
                    f"{passage_id!r} belongs to route {passage['route_id']!r}",
                )
            if passage_id in referenced_passages:
                _fail(
                    "world.json.routes",
                    f"hostile passage {passage_id!r} is listed more than once",
                )
            referenced_passages.add(passage_id)
    missing_passages = set(hostile_passages) - referenced_passages
    if missing_passages:
        _fail(
            "world.json.hostile_passages",
            f"passages not attached to their routes: {', '.join(sorted(missing_passages))}",
        )

    carriage_entries = _array(root["carriages"], "world.json.carriages", minimum=1)
    for index, carriage in enumerate(carriage_entries):
        path = f"world.json.carriages[{index}]"
        item = _closed_object(
            carriage,
            path,
            required={
                "id",
                "name",
                "operator_npc_id",
                "stop_location_ids",
                "route_ids",
                "capacity",
                "fare_coin",
                "operating_windows",
                "departures",
                "layover_minutes",
                "serves_generated_frontier",
                "service_rules",
                "description",
            },
        )
        _identifier(item["id"], f"{path}.id")
        _string(item["name"], f"{path}.name")
        _identifier(item["operator_npc_id"], f"{path}.operator_npc_id")
        stop_location_ids = _string_array(
            item["stop_location_ids"],
            f"{path}.stop_location_ids",
            identifiers=True,
            minimum=2,
        )
        for stop_index, stop_location_id in enumerate(stop_location_ids):
            location = locations.get(stop_location_id)
            if location is None:
                _fail(
                    f"{path}.stop_location_ids[{stop_index}]",
                    f"unknown location {stop_location_id!r}",
                )
            if "carriage_stop" not in location["tags"]:
                _fail(
                    f"{path}.stop_location_ids[{stop_index}]",
                    "carriage stops must carry the carriage_stop location tag",
                )
        route_ids = _string_array(
            item["route_ids"],
            f"{path}.route_ids",
            identifiers=True,
            minimum=1,
        )
        for route_index, route_id in enumerate(route_ids):
            if route_id not in routes:
                _fail(f"{path}.route_ids[{route_index}]", f"unknown route {route_id!r}")
        for first, second in zip(route_ids, route_ids[1:]):
            first_endpoints = {
                routes[first]["from_location_id"],
                routes[first]["to_location_id"],
            }
            second_endpoints = {
                routes[second]["from_location_id"],
                routes[second]["to_location_id"],
            }
            if not first_endpoints & second_endpoints:
                _fail(
                    f"{path}.route_ids",
                    f"routes {first!r} and {second!r} do not form a continuous itinerary",
                )
        if len(stop_location_ids) != len(route_ids) + 1:
            _fail(
                f"{path}.stop_location_ids",
                "a linear service needs one more stop than route segments",
            )
        for route_index, route_id in enumerate(route_ids):
            route_endpoints = {
                routes[route_id]["from_location_id"],
                routes[route_id]["to_location_id"],
            }
            if route_endpoints != {
                stop_location_ids[route_index],
                stop_location_ids[route_index + 1],
            }:
                _fail(
                    f"{path}.stop_location_ids",
                    f"stops do not follow route {route_id!r} in itinerary order",
                )
        _integer(item["capacity"], f"{path}.capacity", minimum=1, maximum=20)
        _integer(item["fare_coin"], f"{path}.fare_coin", minimum=0)
        operating_windows = _array(
            item["operating_windows"],
            f"{path}.operating_windows",
            minimum=1,
        )
        validated_windows: list[tuple[str, int, int, str]] = []
        for window_index, operating_window in enumerate(operating_windows):
            window_path = f"{path}.operating_windows[{window_index}]"
            value = _closed_object(
                operating_window,
                window_path,
                required={"day", "start_minute", "end_minute", "from_location_id"},
            )
            day = _enum(value["day"], f"{window_path}.day", DAY_NAMES)
            start_minute = _integer(
                value["start_minute"],
                f"{window_path}.start_minute",
                minimum=0,
                maximum=1438,
            )
            end_minute = _integer(
                value["end_minute"],
                f"{window_path}.end_minute",
                minimum=1,
                maximum=1439,
            )
            if start_minute >= end_minute:
                _fail(window_path, "start_minute must precede end_minute")
            from_location_id = _identifier(
                value["from_location_id"],
                f"{window_path}.from_location_id",
            )
            if from_location_id not in {stop_location_ids[0], stop_location_ids[-1]}:
                _fail(
                    f"{window_path}.from_location_id",
                    "must be a terminal stop for this linear service",
                )
            window_tuple = (day, start_minute, end_minute, from_location_id)
            if window_tuple in validated_windows:
                _fail(f"{path}.operating_windows", "must not repeat a window")
            validated_windows.append(window_tuple)
        departures = _array(item["departures"], f"{path}.departures", minimum=1)
        for departure_index, departure in enumerate(departures):
            departure_path = f"{path}.departures[{departure_index}]"
            value = _closed_object(
                departure,
                departure_path,
                required={"day", "minute", "from_location_id"},
            )
            _enum(value["day"], f"{departure_path}.day", DAY_NAMES)
            _integer(
                value["minute"],
                f"{departure_path}.minute",
                minimum=0,
                maximum=1439,
            )
            departure_location = _identifier(
                value["from_location_id"],
                f"{departure_path}.from_location_id",
            )
            first_route = routes[route_ids[0]]
            last_route = routes[route_ids[-1]]
            itinerary_terminals = (
                {
                    first_route["from_location_id"],
                    first_route["to_location_id"],
                    last_route["from_location_id"],
                    last_route["to_location_id"],
                }
                - (
                    {
                        first_route["from_location_id"],
                        first_route["to_location_id"],
                    }
                    & {
                        last_route["from_location_id"],
                        last_route["to_location_id"],
                    }
                    if len(route_ids) > 1
                    else set()
                )
            )
            if departure_location not in itinerary_terminals:
                _fail(
                    f"{departure_path}.from_location_id",
                    "must be a terminal endpoint of the itinerary",
                )
            if not any(
                day == value["day"]
                and source == departure_location
                and start <= value["minute"] <= end
                for day, start, end, source in validated_windows
            ):
                _fail(
                    departure_path,
                    "departure must fall inside a matching operating window",
                )
        _integer(item["layover_minutes"], f"{path}.layover_minutes", minimum=0)
        _boolean(
            item["serves_generated_frontier"],
            f"{path}.serves_generated_frontier",
        )
        rules = _closed_object(
            item["service_rules"],
            f"{path}.service_rules",
            required={
                "minimum_passengers",
                "cancels_at_pressure",
                "waits_minutes",
                "refuses_threats",
            },
        )
        _integer(
            rules["minimum_passengers"],
            f"{path}.service_rules.minimum_passengers",
            minimum=0,
            maximum=item["capacity"],
        )
        _integer(
            rules["cancels_at_pressure"],
            f"{path}.service_rules.cancels_at_pressure",
            minimum=1,
            maximum=101,
        )
        _integer(
            rules["waits_minutes"],
            f"{path}.service_rules.waits_minutes",
            minimum=0,
            maximum=240,
        )
        _string_array(
            rules["refuses_threats"],
            f"{path}.service_rules.refuses_threats",
            choices=THREAT_KINDS,
        )
        _string(item["description"], f"{path}.description")
    carriages = _index(carriage_entries, "world.json.carriages")
    if not any(
        carriage["serves_generated_frontier"] for carriage in carriages.values()
    ):
        _fail(
            "world.json.carriages",
            "at least one service must connect eligible generated frontier stops",
        )

    road_neighbors: dict[str, set[str]] = {
        location_id: set() for location_id in locations
    }
    for route in routes.values():
        source = route["from_location_id"]
        target = route["to_location_id"]
        road_neighbors[source].add(target)
        road_neighbors[target].add(source)
    reached_locations = {next(iter(locations))}
    location_frontier = list(reached_locations)
    while location_frontier:
        current = location_frontier.pop()
        for neighbor in road_neighbors[current] - reached_locations:
            reached_locations.add(neighbor)
            location_frontier.append(neighbor)
    if reached_locations != set(locations):
        missing = set(locations) - reached_locations
        _fail(
            "world.json.routes",
            f"locations outside the traversable road graph: {', '.join(sorted(missing))}",
        )

    # It must be possible to move news and people between every authored
    # kingdom without teleporting. Internal district completeness belongs to
    # each region map, not this coarse world graph.
    kingdom_neighbors: dict[str, set[str]] = {kingdom_id: set() for kingdom_id in kingdoms}
    for route in routes.values():
        first = locations[route["from_location_id"]]["kingdom_id"]
        second = locations[route["to_location_id"]]["kingdom_id"]
        if first != second:
            kingdom_neighbors[first].add(second)
            kingdom_neighbors[second].add(first)
    reached = {next(iter(kingdoms))}
    frontier = list(reached)
    while frontier:
        current = frontier.pop()
        for neighbor in kingdom_neighbors[current] - reached:
            reached.add(neighbor)
            frontier.append(neighbor)
    if reached != set(kingdoms):
        _fail("world.json.routes", "the authored kingdoms must form one connected road graph")

    return kingdoms, locations, routes, hostile_passages, carriages


def _validate_npcs(
    document: dict[str, Any],
    *,
    locations: Mapping[str, dict[str, Any]],
    routes: Mapping[str, dict[str, Any]],
    hostile_passages: Mapping[str, dict[str, Any]],
    core_npcs: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    root = _validate_schema_root(document, "npc_profiles.json", "profiles")
    profile_entries = root["profiles"]
    for index, profile in enumerate(profile_entries):
        path = f"npc_profiles.json.profiles[{index}]"
        item = _closed_object(
            profile,
            path,
            required={
                "id",
                "name",
                "role",
                "kind",
                "home_location_id",
                "deliberation_windows",
                "schedule",
                "needs",
                "private_goals",
                "movement",
                "belief_refs",
                "trigger_ids",
                "offscreen_policy",
            },
        )
        npc_id = _identifier(item["id"], f"{path}.id")
        _string(item["name"], f"{path}.name")
        _string(item["role"], f"{path}.role")
        _enum(item["kind"], f"{path}.kind", NPC_KINDS)
        home = _identifier(item["home_location_id"], f"{path}.home_location_id")
        if home not in locations:
            _fail(f"{path}.home_location_id", f"unknown location {home!r}")

        deliberations = _array(
            item["deliberation_windows"],
            f"{path}.deliberation_windows",
            minimum=3,
            maximum=6,
        )
        deliberation_minutes: list[int] = []
        for window_index, window in enumerate(deliberations):
            window_path = f"{path}.deliberation_windows[{window_index}]"
            value = _closed_object(
                window,
                window_path,
                required={"minute", "purpose"},
            )
            deliberation_minutes.append(
                _integer(
                    value["minute"],
                    f"{window_path}.minute",
                    minimum=0,
                    maximum=1439,
                )
            )
            _enum(
                value["purpose"],
                f"{window_path}.purpose",
                DELIBERATION_PURPOSES,
            )
        if deliberation_minutes != sorted(set(deliberation_minutes)):
            _fail(
                f"{path}.deliberation_windows",
                "minutes must be unique and in ascending order",
            )

        schedule = _array(item["schedule"], f"{path}.schedule", minimum=3)
        schedule_minutes: list[int] = []
        for anchor_index, anchor in enumerate(schedule):
            anchor_path = f"{path}.schedule[{anchor_index}]"
            value = _closed_object(
                anchor,
                anchor_path,
                required={"start_minute", "location_id", "activity", "commitment"},
            )
            schedule_minutes.append(
                _integer(
                    value["start_minute"],
                    f"{anchor_path}.start_minute",
                    minimum=0,
                    maximum=1439,
                )
            )
            location_id = _identifier(
                value["location_id"],
                f"{anchor_path}.location_id",
            )
            if location_id not in locations:
                _fail(f"{anchor_path}.location_id", f"unknown location {location_id!r}")
            _enum(value["activity"], f"{anchor_path}.activity", SCHEDULE_ACTIVITIES)
            _integer(
                value["commitment"],
                f"{anchor_path}.commitment",
                minimum=0,
                maximum=100,
            )
        if schedule_minutes != sorted(set(schedule_minutes)):
            _fail(f"{path}.schedule", "anchors must be unique and in ascending order")

        needs = _array(item["needs"], f"{path}.needs", minimum=2)
        need_kinds: list[str] = []
        for need_index, need in enumerate(needs):
            need_path = f"{path}.needs[{need_index}]"
            value = _closed_object(
                need,
                need_path,
                required={"kind", "weight", "satisfiers"},
            )
            need_kinds.append(_enum(value["kind"], f"{need_path}.kind", NEED_KINDS))
            _integer(value["weight"], f"{need_path}.weight", minimum=1, maximum=100)
            _string_array(
                value["satisfiers"],
                f"{need_path}.satisfiers",
                choices=SATISFIER_ACTIONS,
                minimum=1,
            )
        if len(need_kinds) != len(set(need_kinds)):
            _fail(f"{path}.needs", "need kinds must be unique")

        goals = _array(item["private_goals"], f"{path}.private_goals", minimum=1)
        goal_ids: set[str] = set()
        for goal_index, goal in enumerate(goals):
            goal_path = f"{path}.private_goals[{goal_index}]"
            value = _closed_object(
                goal,
                goal_path,
                required={
                    "id",
                    "desire",
                    "priority",
                    "approach",
                    "target",
                    "risk_tolerance",
                },
            )
            goal_id = _identifier(value["id"], f"{goal_path}.id")
            if goal_id in goal_ids:
                _fail(f"{path}.private_goals", f"duplicate goal id {goal_id!r}")
            goal_ids.add(goal_id)
            _string(value["desire"], f"{goal_path}.desire")
            _integer(value["priority"], f"{goal_path}.priority", minimum=1, maximum=5)
            _enum(value["approach"], f"{goal_path}.approach", GOAL_APPROACHES)
            target = _closed_object(
                value["target"],
                f"{goal_path}.target",
                required={"kind", "id"},
            )
            target_kind = _enum(
                target["kind"],
                f"{goal_path}.target.kind",
                GOAL_TARGET_KINDS,
            )
            target_id = _identifier(target["id"], f"{goal_path}.target.id")
            if target_kind == "location" and target_id not in locations:
                _fail(f"{goal_path}.target.id", f"unknown location {target_id!r}")
            if target_kind == "self" and target_id != npc_id:
                _fail(f"{goal_path}.target.id", "self targets must use the profile id")
            _enum(
                value["risk_tolerance"],
                f"{goal_path}.risk_tolerance",
                RISK_TOLERANCES,
            )

        movement = _closed_object(
            item["movement"],
            f"{path}.movement",
            required={
                "travel_modes",
                "maximum_route_risk",
                "avoid_threats",
                "fallback_location_id",
            },
        )
        _string_array(
            movement["travel_modes"],
            f"{path}.movement.travel_modes",
            choices=TRAVEL_MODES,
            minimum=1,
        )
        _enum(
            movement["maximum_route_risk"],
            f"{path}.movement.maximum_route_risk",
            ROUTE_RISKS,
        )
        _string_array(
            movement["avoid_threats"],
            f"{path}.movement.avoid_threats",
            choices=THREAT_KINDS,
        )
        fallback = _identifier(
            movement["fallback_location_id"],
            f"{path}.movement.fallback_location_id",
        )
        if fallback not in locations:
            _fail(
                f"{path}.movement.fallback_location_id",
                f"unknown location {fallback!r}",
            )

        belief_refs = _array(item["belief_refs"], f"{path}.belief_refs")
        seen_belief_refs: set[tuple[str, str]] = set()
        for belief_index, belief_ref in enumerate(belief_refs):
            belief_path = f"{path}.belief_refs[{belief_index}]"
            value = _closed_object(
                belief_ref,
                belief_path,
                required={"rumor_id", "belief_id"},
            )
            ref = (
                _identifier(value["rumor_id"], f"{belief_path}.rumor_id"),
                _identifier(value["belief_id"], f"{belief_path}.belief_id"),
            )
            if ref in seen_belief_refs:
                _fail(f"{path}.belief_refs", "must not contain duplicates")
            seen_belief_refs.add(ref)

        _string_array(item["trigger_ids"], f"{path}.trigger_ids", identifiers=True)
        policy = _closed_object(
            item["offscreen_policy"],
            f"{path}.offscreen_policy",
            required={
                "can_relocate",
                "can_die",
                "missed_windows_are_final",
                "minimum_warning_memories",
            },
        )
        _boolean(policy["can_relocate"], f"{path}.offscreen_policy.can_relocate")
        can_die = _boolean(policy["can_die"], f"{path}.offscreen_policy.can_die")
        _boolean(
            policy["missed_windows_are_final"],
            f"{path}.offscreen_policy.missed_windows_are_final",
        )
        warning_count = _integer(
            policy["minimum_warning_memories"],
            f"{path}.offscreen_policy.minimum_warning_memories",
            minimum=0,
            maximum=3,
        )
        if can_die and warning_count < 1:
            _fail(
                f"{path}.offscreen_policy.minimum_warning_memories",
                "off-screen death requires at least one prior warning memory",
            )
    profiles = _index(profile_entries, "npc_profiles.json.profiles")

    missing_core = set(core_npcs) - set(profiles)
    if missing_core:
        _fail(
            "npc_profiles.json.profiles",
            f"missing profiles for core NPCs: {', '.join(sorted(missing_core))}",
        )
    for core_id, core in core_npcs.items():
        if profiles[core_id]["name"] != core.get("name"):
            _fail(
                f"npc_profiles.json.profiles[{core_id}].name",
                "must match content/npcs.json",
            )

    npc_ids = set(profiles)
    location_ids = set(locations)
    for npc_id, profile in profiles.items():
        for goal in profile["private_goals"]:
            target = goal["target"]
            if target["kind"] == "npc" and target["id"] not in npc_ids:
                _fail(
                    f"npc_profiles.json.profiles[{npc_id}].private_goals[{goal['id']}].target.id",
                    f"unknown NPC {target['id']!r}",
                )
            if target["kind"] == "kingdom":
                # Kingdom targets are checked later against the world document
                # through their location-derived ids in ``validate``.
                continue
            if target["kind"] == "location" and target["id"] not in location_ids:
                _fail(
                    f"npc_profiles.json.profiles[{npc_id}].private_goals[{goal['id']}].target.id",
                    f"unknown location {target['id']!r}",
                )
        maximum = ROUTE_RISK_RANK[profile["movement"]["maximum_route_risk"]]
        for mode in profile["movement"]["travel_modes"]:
            if mode == "carriage" and not routes:
                _fail(
                    f"npc_profiles.json.profiles[{npc_id}].movement.travel_modes",
                    "carriage travel requires authored routes",
                )
        # Avoiding every danger on all traversable routes is valid: the
        # deterministic planner will keep the person home rather than invent
        # a shortcut.
        _ = maximum, hostile_passages
    return profiles


def _validate_rumors(
    document: dict[str, Any],
    *,
    npc_profiles: Mapping[str, dict[str, Any]],
    locations: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    root = _validate_schema_root(document, "rumors.json", "rumors")
    rumor_entries = root["rumors"]
    for index, rumor in enumerate(rumor_entries):
        path = f"rumors.json.rumors[{index}]"
        item = _closed_object(
            rumor,
            path,
            required={"id", "topic", "truth", "beliefs", "transmission"},
        )
        _identifier(item["id"], f"{path}.id")
        _string(item["topic"], f"{path}.topic")
        truth = _closed_object(
            item["truth"],
            f"{path}.truth",
            required={"classification", "account", "evidence"},
        )
        _enum(
            truth["classification"],
            f"{path}.truth.classification",
            RUMOR_TRUTH,
        )
        _string(truth["account"], f"{path}.truth.account")
        _string_array(truth["evidence"], f"{path}.truth.evidence", minimum=1)

        beliefs = _array(item["beliefs"], f"{path}.beliefs", minimum=1)
        belief_ids: set[str] = set()
        holders: set[str] = set()
        for belief_index, belief in enumerate(beliefs):
            belief_path = f"{path}.beliefs[{belief_index}]"
            value = _closed_object(
                belief,
                belief_path,
                required={
                    "id",
                    "holder_npc_id",
                    "claim",
                    "confidence",
                    "truth_alignment",
                    "source",
                },
            )
            belief_id = _identifier(value["id"], f"{belief_path}.id")
            if belief_id in belief_ids:
                _fail(f"{path}.beliefs", f"duplicate belief id {belief_id!r}")
            belief_ids.add(belief_id)
            holder = _identifier(
                value["holder_npc_id"],
                f"{belief_path}.holder_npc_id",
            )
            if holder not in npc_profiles:
                _fail(f"{belief_path}.holder_npc_id", f"unknown NPC {holder!r}")
            if holder in holders:
                _fail(f"{path}.beliefs", f"NPC {holder!r} has multiple initial versions")
            holders.add(holder)
            _string(value["claim"], f"{belief_path}.claim")
            _integer(
                value["confidence"],
                f"{belief_path}.confidence",
                minimum=0,
                maximum=100,
            )
            _enum(
                value["truth_alignment"],
                f"{belief_path}.truth_alignment",
                TRUTH_ALIGNMENT,
            )
            source = _closed_object(
                value["source"],
                f"{belief_path}.source",
                required={"kind", "ref", "chain"},
            )
            source_kind = _enum(
                source["kind"],
                f"{belief_path}.source.kind",
                SOURCE_KINDS,
            )
            source_ref = _string(source["ref"], f"{belief_path}.source.ref")
            chain = _string_array(
                source["chain"],
                f"{belief_path}.source.chain",
                identifiers=True,
            )
            for chain_index, npc_id in enumerate(chain):
                if npc_id not in npc_profiles:
                    _fail(
                        f"{belief_path}.source.chain[{chain_index}]",
                        f"unknown NPC {npc_id!r}",
                    )
            if holder in chain:
                _fail(f"{belief_path}.source.chain", "must not include its holder")
            if source_kind == "npc":
                if source_ref not in npc_profiles:
                    _fail(f"{belief_path}.source.ref", f"unknown NPC {source_ref!r}")
                if not chain or chain[0] != source_ref:
                    _fail(
                        f"{belief_path}.source.chain",
                        "NPC sources must begin their source chain",
                    )
            elif source_kind in {"firsthand", "place", "official_notice"}:
                if source_ref not in locations:
                    _fail(
                        f"{belief_path}.source.ref",
                        f"unknown location {source_ref!r}",
                    )
            elif source_kind == "anonymous" and source_ref != "unknown":
                _fail(f"{belief_path}.source.ref", "anonymous sources must use 'unknown'")

        transmission = _closed_object(
            item["transmission"],
            f"{path}.transmission",
            required={"share_threshold", "distortion", "contexts"},
        )
        _integer(
            transmission["share_threshold"],
            f"{path}.transmission.share_threshold",
            minimum=0,
            maximum=100,
        )
        _enum(
            transmission["distortion"],
            f"{path}.transmission.distortion",
            DISTORTION_KINDS,
        )
        _string_array(
            transmission["contexts"],
            f"{path}.transmission.contexts",
            choices=RUMOR_CONTEXTS,
            minimum=1,
        )
    rumors = _index(rumor_entries, "rumors.json.rumors")

    for npc_id, profile in npc_profiles.items():
        for index, belief_ref in enumerate(profile["belief_refs"]):
            rumor_id = belief_ref["rumor_id"]
            rumor = rumors.get(rumor_id)
            path = f"npc_profiles.json.profiles[{npc_id}].belief_refs[{index}]"
            if rumor is None:
                _fail(f"{path}.rumor_id", f"unknown rumor {rumor_id!r}")
            matches = [
                belief
                for belief in rumor["beliefs"]
                if belief["id"] == belief_ref["belief_id"]
            ]
            if not matches:
                _fail(
                    f"{path}.belief_id",
                    f"unknown belief {belief_ref['belief_id']!r} in {rumor_id!r}",
                )
            if matches[0]["holder_npc_id"] != npc_id:
                _fail(f"{path}.belief_id", "belief holder must match the profile")

    for rumor_id, rumor in rumors.items():
        for belief in rumor["beliefs"]:
            profile = npc_profiles[belief["holder_npc_id"]]
            expected = {"rumor_id": rumor_id, "belief_id": belief["id"]}
            if expected not in profile["belief_refs"]:
                _fail(
                    f"rumors.json.rumors[{rumor_id}].beliefs[{belief['id']}]",
                    "holder profile must reference this initial belief",
                )
    return rumors


def _validate_condition(
    condition: Any,
    path: str,
    *,
    npc_ids: set[str],
    location_ids: set[str],
    rumor_ids: set[str],
    carriage_ids: set[str],
    passage_ids: set[str],
) -> str:
    if not isinstance(condition, dict):
        _fail(path, "must be an object")
    kind = _enum(
        condition.get("kind"),
        f"{path}.kind",
        {
            "believes",
            "carriage_arrives",
            "co_located",
            "day_phase",
            "fact_absent",
            "fact_equals",
            "fact_exists",
            "npc_alive",
            "npc_at",
            "npc_health_at_most",
            "route_pressure_at_least",
            "trigger_fired",
        },
    )
    schemas = {
        "believes": {"kind", "npc_id", "rumor_id", "minimum_confidence"},
        "carriage_arrives": {"kind", "carriage_id", "location_id"},
        "co_located": {"kind", "npc_ids"},
        "day_phase": {"kind", "phases"},
        "fact_absent": {"kind", "fact_key"},
        "fact_equals": {"kind", "fact_key", "value"},
        "fact_exists": {"kind", "fact_key"},
        "npc_alive": {"kind", "npc_id", "value"},
        "npc_at": {"kind", "npc_id", "location_id"},
        "npc_health_at_most": {"kind", "npc_id", "hp"},
        "route_pressure_at_least": {"kind", "hostile_passage_id", "minimum"},
        "trigger_fired": {"kind", "trigger_id"},
    }
    item = _closed_object(condition, path, required=schemas[kind])

    if kind == "believes":
        npc_id = _identifier(item["npc_id"], f"{path}.npc_id")
        rumor_id = _identifier(item["rumor_id"], f"{path}.rumor_id")
        if npc_id not in npc_ids:
            _fail(f"{path}.npc_id", f"unknown NPC {npc_id!r}")
        if rumor_id not in rumor_ids:
            _fail(f"{path}.rumor_id", f"unknown rumor {rumor_id!r}")
        _integer(
            item["minimum_confidence"],
            f"{path}.minimum_confidence",
            minimum=0,
            maximum=100,
        )
    elif kind == "carriage_arrives":
        carriage_id = _identifier(item["carriage_id"], f"{path}.carriage_id")
        location_id = _identifier(item["location_id"], f"{path}.location_id")
        if carriage_id not in carriage_ids:
            _fail(f"{path}.carriage_id", f"unknown carriage {carriage_id!r}")
        if location_id not in location_ids:
            _fail(f"{path}.location_id", f"unknown location {location_id!r}")
    elif kind == "co_located":
        refs = _string_array(
            item["npc_ids"],
            f"{path}.npc_ids",
            identifiers=True,
            minimum=2,
        )
        for index, npc_id in enumerate(refs):
            if npc_id not in npc_ids:
                _fail(f"{path}.npc_ids[{index}]", f"unknown NPC {npc_id!r}")
    elif kind == "day_phase":
        _string_array(
            item["phases"],
            f"{path}.phases",
            choices=DAY_PHASES,
            minimum=1,
        )
    elif kind in {"fact_absent", "fact_exists", "fact_equals"}:
        _string(item["fact_key"], f"{path}.fact_key")
        if kind == "fact_equals" and not isinstance(item["value"], dict):
            _fail(f"{path}.value", "must be an object")
    elif kind == "npc_alive":
        npc_id = _identifier(item["npc_id"], f"{path}.npc_id")
        if npc_id not in npc_ids:
            _fail(f"{path}.npc_id", f"unknown NPC {npc_id!r}")
        _boolean(item["value"], f"{path}.value")
    elif kind == "npc_at":
        npc_id = _identifier(item["npc_id"], f"{path}.npc_id")
        location_id = _identifier(item["location_id"], f"{path}.location_id")
        if npc_id not in npc_ids:
            _fail(f"{path}.npc_id", f"unknown NPC {npc_id!r}")
        if location_id not in location_ids:
            _fail(f"{path}.location_id", f"unknown location {location_id!r}")
    elif kind == "npc_health_at_most":
        npc_id = _identifier(item["npc_id"], f"{path}.npc_id")
        if npc_id not in npc_ids:
            _fail(f"{path}.npc_id", f"unknown NPC {npc_id!r}")
        _integer(item["hp"], f"{path}.hp", minimum=0)
    elif kind == "route_pressure_at_least":
        passage_id = _identifier(
            item["hostile_passage_id"],
            f"{path}.hostile_passage_id",
        )
        if passage_id not in passage_ids:
            _fail(
                f"{path}.hostile_passage_id",
                f"unknown hostile passage {passage_id!r}",
            )
        _integer(item["minimum"], f"{path}.minimum", minimum=0, maximum=100)
    else:
        _identifier(item["trigger_id"], f"{path}.trigger_id")
    return kind


def _validate_effect(
    effect: Any,
    path: str,
    *,
    npc_ids: set[str],
    location_ids: set[str],
    rumor_ids: set[str],
    carriage_ids: set[str],
    npc_goal_ids: Mapping[str, set[str]],
) -> str:
    if not isinstance(effect, dict):
        _fail(path, "must be an object")
    kind = _enum(
        effect.get("kind"),
        f"{path}.kind",
        {
            "board_carriage",
            "change_need",
            "disappear_npc",
            "kill_npc",
            "leave_evidence",
            "relocate_npc",
            "relationship_shift",
            "remember",
            "set_disposition",
            "set_direction",
            "set_fact",
            "set_goal_status",
            "share_rumor",
            "wound_npc",
        },
    )
    schemas = {
        "board_carriage": {"kind", "npc_id", "carriage_id", "destination_location_id"},
        "change_need": {"kind", "npc_id", "need", "delta"},
        "disappear_npc": {"kind", "npc_id", "location_id", "reason"},
        "kill_npc": {"kind", "npc_id", "summary"},
        "leave_evidence": {"kind", "location_id", "description"},
        "relocate_npc": {"kind", "npc_id", "location_id", "reason"},
        "relationship_shift": {
            "kind",
            "from_npc_id",
            "to_npc_id",
            "axis",
            "delta",
        },
        "remember": {"kind", "npc_id", "summary", "importance", "tags"},
        "set_disposition": {"kind", "npc_id", "disposition"},
        "set_direction": {"kind", "npc_id", "location_id", "reason"},
        "set_fact": {
            "kind",
            "fact_key",
            "subject_id",
            "predicate",
            "value",
        },
        "set_goal_status": {
            "kind",
            "npc_id",
            "goal_id",
            "status",
            "reason",
        },
        "share_rumor": {
            "kind",
            "speaker_npc_id",
            "listener_npc_id",
            "rumor_id",
        },
        "wound_npc": {"kind", "npc_id", "damage", "summary"},
    }
    item = _closed_object(effect, path, required=schemas[kind])

    def npc_ref(field: str) -> str:
        npc_id = _identifier(item[field], f"{path}.{field}")
        if npc_id not in npc_ids:
            _fail(f"{path}.{field}", f"unknown NPC {npc_id!r}")
        return npc_id

    def location_ref(field: str) -> str:
        location_id = _identifier(item[field], f"{path}.{field}")
        if location_id not in location_ids:
            _fail(f"{path}.{field}", f"unknown location {location_id!r}")
        return location_id

    if kind == "board_carriage":
        npc_ref("npc_id")
        carriage_id = _identifier(item["carriage_id"], f"{path}.carriage_id")
        if carriage_id not in carriage_ids:
            _fail(f"{path}.carriage_id", f"unknown carriage {carriage_id!r}")
        location_ref("destination_location_id")
    elif kind == "change_need":
        npc_ref("npc_id")
        _enum(item["need"], f"{path}.need", NEED_KINDS)
        delta = _integer(item["delta"], f"{path}.delta", minimum=-100, maximum=100)
        if delta == 0:
            _fail(f"{path}.delta", "must change the need")
    elif kind in {"disappear_npc", "relocate_npc"}:
        npc_ref("npc_id")
        location_ref("location_id")
        _string(item["reason"], f"{path}.reason")
    elif kind == "kill_npc":
        npc_ref("npc_id")
        _string(item["summary"], f"{path}.summary")
    elif kind == "leave_evidence":
        location_ref("location_id")
        _string(item["description"], f"{path}.description")
    elif kind == "relationship_shift":
        first = npc_ref("from_npc_id")
        second = npc_ref("to_npc_id")
        if first == second:
            _fail(path, "relationship shifts require two different people")
        _enum(item["axis"], f"{path}.axis", RELATIONSHIP_AXES)
        delta = _integer(item["delta"], f"{path}.delta", minimum=-25, maximum=25)
        if delta == 0:
            _fail(f"{path}.delta", "must change the relationship")
    elif kind == "remember":
        npc_ref("npc_id")
        _string(item["summary"], f"{path}.summary")
        _integer(item["importance"], f"{path}.importance", minimum=1, maximum=10)
        _string_array(
            item["tags"],
            f"{path}.tags",
            choices=MEMORY_TAGS,
            minimum=1,
        )
    elif kind == "set_disposition":
        npc_ref("npc_id")
        _enum(
            item["disposition"],
            f"{path}.disposition",
            NPC_DISPOSITIONS,
        )
    elif kind == "set_direction":
        npc_ref("npc_id")
        location_ref("location_id")
        _string(item["reason"], f"{path}.reason")
    elif kind == "set_fact":
        _string(item["fact_key"], f"{path}.fact_key")
        _string(item["subject_id"], f"{path}.subject_id")
        _string(item["predicate"], f"{path}.predicate")
        if not isinstance(item["value"], dict):
            _fail(f"{path}.value", "must be an object")
    elif kind == "set_goal_status":
        npc_id = npc_ref("npc_id")
        goal_id = _identifier(item["goal_id"], f"{path}.goal_id")
        if goal_id not in npc_goal_ids.get(npc_id, set()):
            _fail(
                f"{path}.goal_id",
                f"unknown private goal {goal_id!r} for NPC {npc_id!r}",
            )
        _enum(item["status"], f"{path}.status", GOAL_STATUSES)
        _string(item["reason"], f"{path}.reason")
    elif kind == "wound_npc":
        npc_ref("npc_id")
        _integer(item["damage"], f"{path}.damage", minimum=1, maximum=1000)
        _string(item["summary"], f"{path}.summary")
    else:
        speaker = npc_ref("speaker_npc_id")
        listener = npc_ref("listener_npc_id")
        if speaker == listener:
            _fail(path, "a person cannot share a rumor with themselves")
        rumor_id = _identifier(item["rumor_id"], f"{path}.rumor_id")
        if rumor_id not in rumor_ids:
            _fail(f"{path}.rumor_id", f"unknown rumor {rumor_id!r}")
    return kind


def _validate_triggers(
    document: dict[str, Any],
    *,
    npc_profiles: Mapping[str, dict[str, Any]],
    locations: Mapping[str, dict[str, Any]],
    rumors: Mapping[str, dict[str, Any]],
    carriages: Mapping[str, dict[str, Any]],
    hostile_passages: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    root = _validate_schema_root(document, "triggers.json", "triggers")
    trigger_entries = root["triggers"]
    npc_ids = set(npc_profiles)
    location_ids = set(locations)
    rumor_ids = set(rumors)
    carriage_ids = set(carriages)
    passage_ids = set(hostile_passages)
    npc_goal_ids = {
        npc_id: {goal["id"] for goal in profile["private_goals"]}
        for npc_id, profile in npc_profiles.items()
    }
    trigger_ids = {
        _identifier(trigger.get("id"), f"triggers.json.triggers[{index}].id")
        for index, trigger in enumerate(trigger_entries)
        if isinstance(trigger, dict)
    }
    if len(trigger_ids) != len(trigger_entries):
        _fail("triggers.json.triggers", "trigger ids must be unique")

    for index, trigger in enumerate(trigger_entries):
        path = f"triggers.json.triggers[{index}]"
        item = _closed_object(
            trigger,
            path,
            required={
                "id",
                "kind",
                "participants",
                "window",
                "conditions",
                "effects",
                "conversation",
                "missed_consequences",
                "aftermath_clues",
            },
        )
        _identifier(item["id"], f"{path}.id")
        kind = _enum(item["kind"], f"{path}.kind", TRIGGER_KINDS)
        participants = _string_array(
            item["participants"],
            f"{path}.participants",
            identifiers=True,
            minimum=1,
            maximum=4,
        )
        for participant_index, npc_id in enumerate(participants):
            if npc_id not in npc_ids:
                _fail(
                    f"{path}.participants[{participant_index}]",
                    f"unknown NPC {npc_id!r}",
                )

        window = _closed_object(
            item["window"],
            f"{path}.window",
            required={"opens_day", "closes_day", "cooldown_minutes", "max_firings"},
        )
        opens = _integer(window["opens_day"], f"{path}.window.opens_day", minimum=0)
        closes = window["closes_day"]
        if closes is not None:
            closes = _integer(closes, f"{path}.window.closes_day", minimum=opens)
        _integer(
            window["cooldown_minutes"],
            f"{path}.window.cooldown_minutes",
            minimum=0,
        )
        _integer(
            window["max_firings"],
            f"{path}.window.max_firings",
            minimum=1,
            maximum=100,
        )

        conditions = _array(item["conditions"], f"{path}.conditions", minimum=1)
        for condition_index, condition in enumerate(conditions):
            condition_kind = _validate_condition(
                condition,
                f"{path}.conditions[{condition_index}]",
                npc_ids=npc_ids,
                location_ids=location_ids,
                rumor_ids=rumor_ids,
                carriage_ids=carriage_ids,
                passage_ids=passage_ids,
            )
            if condition_kind == "trigger_fired" and condition["trigger_id"] not in trigger_ids:
                _fail(
                    f"{path}.conditions[{condition_index}].trigger_id",
                    f"unknown trigger {condition['trigger_id']!r}",
                )

        effects = _array(item["effects"], f"{path}.effects", minimum=1)
        for effect_index, effect in enumerate(effects):
            _validate_effect(
                effect,
                f"{path}.effects[{effect_index}]",
                npc_ids=npc_ids,
                location_ids=location_ids,
                rumor_ids=rumor_ids,
                carriage_ids=carriage_ids,
                npc_goal_ids=npc_goal_ids,
            )

        conversation = item["conversation"]
        if kind == "conversation":
            value = _closed_object(
                conversation,
                f"{path}.conversation",
                required={
                    "opening_speaker_npc_id",
                    "opening_line",
                    "mode",
                    "max_turns",
                    "topics",
                    "stop_when",
                    "followup_trigger_ids",
                },
            )
            opener = _identifier(
                value["opening_speaker_npc_id"],
                f"{path}.conversation.opening_speaker_npc_id",
            )
            if opener not in participants:
                _fail(
                    f"{path}.conversation.opening_speaker_npc_id",
                    "must be a participant",
                )
            _string(value["opening_line"], f"{path}.conversation.opening_line")
            _enum(
                value["mode"],
                f"{path}.conversation.mode",
                CONVERSATION_MODES,
            )
            _integer(
                value["max_turns"],
                f"{path}.conversation.max_turns",
                minimum=2,
                maximum=8,
            )
            _string_array(
                value["topics"],
                f"{path}.conversation.topics",
                choices=CONVERSATION_TOPICS,
                minimum=1,
            )
            _string_array(
                value["stop_when"],
                f"{path}.conversation.stop_when",
                choices=CONVERSATION_STOPS,
                minimum=1,
            )
            followups = _string_array(
                value["followup_trigger_ids"],
                f"{path}.conversation.followup_trigger_ids",
                identifiers=True,
            )
            for followup_index, followup in enumerate(followups):
                if followup not in trigger_ids:
                    _fail(
                        f"{path}.conversation.followup_trigger_ids[{followup_index}]",
                        f"unknown trigger {followup!r}",
                    )
        elif conversation is not None:
            _fail(f"{path}.conversation", "story triggers must use null")

        missed = _array(item["missed_consequences"], f"{path}.missed_consequences")
        for effect_index, effect in enumerate(missed):
            _validate_effect(
                effect,
                f"{path}.missed_consequences[{effect_index}]",
                npc_ids=npc_ids,
                location_ids=location_ids,
                rumor_ids=rumor_ids,
                carriage_ids=carriage_ids,
                npc_goal_ids=npc_goal_ids,
            )
        clues = _array(item["aftermath_clues"], f"{path}.aftermath_clues")
        for clue_index, clue in enumerate(clues):
            clue_path = f"{path}.aftermath_clues[{clue_index}]"
            value = _closed_object(
                clue,
                clue_path,
                required={"location_id", "description", "discoverable_for_days"},
            )
            location_id = _identifier(
                value["location_id"],
                f"{clue_path}.location_id",
            )
            if location_id not in location_ids:
                _fail(f"{clue_path}.location_id", f"unknown location {location_id!r}")
            _string(value["description"], f"{clue_path}.description")
            _integer(
                value["discoverable_for_days"],
                f"{clue_path}.discoverable_for_days",
                minimum=1,
            )
        if (missed or clues) and closes is None:
            _fail(
                f"{path}.window.closes_day",
                "missed consequences require a finite opportunity window",
            )
    triggers = _index(trigger_entries, "triggers.json.triggers")

    subscriptions: dict[str, set[str]] = {npc_id: set() for npc_id in npc_ids}
    for npc_id, profile in npc_profiles.items():
        for trigger_id in profile["trigger_ids"]:
            if trigger_id not in triggers:
                _fail(
                    f"npc_profiles.json.profiles[{npc_id}].trigger_ids",
                    f"unknown trigger {trigger_id!r}",
                )
            subscriptions[npc_id].add(trigger_id)
    for trigger_id, trigger in triggers.items():
        for npc_id in trigger["participants"]:
            if trigger_id not in subscriptions[npc_id]:
                _fail(
                    f"npc_profiles.json.profiles[{npc_id}].trigger_ids",
                    f"must subscribe to participant trigger {trigger_id!r}",
                )

    operator_ids = {carriage["operator_npc_id"] for carriage in carriages.values()}
    missing_operators = operator_ids - npc_ids
    if missing_operators:
        _fail(
            "world.json.carriages",
            f"operators without NPC profiles: {', '.join(sorted(missing_operators))}",
        )
    return triggers


def validate_living_world_content(
    *,
    world_document: dict[str, Any],
    npc_document: dict[str, Any],
    rumor_document: dict[str, Any],
    trigger_document: dict[str, Any],
    core_npcs: Mapping[str, dict[str, Any]],
) -> LivingWorldContent:
    """Validate all four documents and their cross-document references."""

    documents = (world_document, npc_document, rumor_document, trigger_document)
    for document in documents:
        _reject_forbidden_keys(document)

    kingdoms, locations, routes, hostile_passages, carriages = _validate_world(
        world_document
    )
    npc_profiles = _validate_npcs(
        npc_document,
        locations=locations,
        routes=routes,
        hostile_passages=hostile_passages,
        core_npcs=core_npcs,
    )
    rumors = _validate_rumors(
        rumor_document,
        npc_profiles=npc_profiles,
        locations=locations,
    )
    triggers = _validate_triggers(
        trigger_document,
        npc_profiles=npc_profiles,
        locations=locations,
        rumors=rumors,
        carriages=carriages,
        hostile_passages=hostile_passages,
    )

    kingdom_ids = set(kingdoms)
    for npc_id, profile in npc_profiles.items():
        for goal in profile["private_goals"]:
            target = goal["target"]
            if target["kind"] == "kingdom" and target["id"] not in kingdom_ids:
                _fail(
                    f"npc_profiles.json.profiles[{npc_id}].private_goals[{goal['id']}].target.id",
                    f"unknown kingdom {target['id']!r}",
                )
            if target["kind"] == "rumor" and target["id"] not in rumors:
                _fail(
                    f"npc_profiles.json.profiles[{npc_id}].private_goals[{goal['id']}].target.id",
                    f"unknown rumor {target['id']!r}",
                )

    # A travel risk above an NPC's tolerance is a hard path-planner boundary,
    # not prose advice.  At least one profile must be able to traverse each
    # inter-kingdom route or rumors can never move through the simulation.
    for route_id, route in routes.items():
        source_kingdom = locations[route["from_location_id"]]["kingdom_id"]
        target_kingdom = locations[route["to_location_id"]]["kingdom_id"]
        if source_kingdom == target_kingdom:
            continue
        route_rank = ROUTE_RISK_RANK[route["risk"]]
        if not any(
            ROUTE_RISK_RANK[profile["movement"]["maximum_route_risk"]] >= route_rank
            for profile in npc_profiles.values()
        ):
            _fail(
                f"world.json.routes[{route_id}].risk",
                "no authored NPC is willing to traverse this inter-kingdom route",
            )

    for carriage_id, carriage in carriages.items():
        operator = npc_profiles[carriage["operator_npc_id"]]
        operator_risk = ROUTE_RISK_RANK[
            operator["movement"]["maximum_route_risk"]
        ]
        service_route_risk = max(
            ROUTE_RISK_RANK[routes[route_id]["risk"]]
            for route_id in carriage["route_ids"]
        )
        if operator_risk < service_route_risk:
            _fail(
                f"world.json.carriages[{carriage_id}].operator_npc_id",
                "operator risk tolerance is below their service route",
            )
        route_passages = [
            hostile_passages[passage_id]
            for route_id in carriage["route_ids"]
            for passage_id in routes[route_id]["hostile_passage_ids"]
        ]
        active_threats = {
            threat
            for passage in route_passages
            for threat in passage["threats"]
        }
        refused = set(carriage["service_rules"]["refuses_threats"])
        if refused & active_threats:
            _fail(
                f"world.json.carriages[{carriage_id}].service_rules.refuses_threats",
                "service refuses a threat permanently present on its own route",
            )
        maximum_pressure = max(
            (passage["encounter_pressure"] for passage in route_passages),
            default=0,
        )
        if carriage["service_rules"]["cancels_at_pressure"] <= maximum_pressure:
            _fail(
                f"world.json.carriages[{carriage_id}].service_rules.cancels_at_pressure",
                "service would always cancel on its authored route",
            )

    return LivingWorldContent(
        world_document=world_document,
        npc_document=npc_document,
        rumor_document=rumor_document,
        trigger_document=trigger_document,
        carriage_network=world_document["carriage_network"],
        kingdoms=kingdoms,
        locations=locations,
        routes=routes,
        hostile_passages=hostile_passages,
        carriages=carriages,
        npc_profiles=npc_profiles,
        rumors=rumors,
        triggers=triggers,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise LivingWorldContentError(f"missing authored content file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LivingWorldContentError(
            f"invalid authored content JSON in {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise LivingWorldContentError(f"{path}: root must be an object")
    return value


def _load_core_npcs(content_root: Path) -> dict[str, dict[str, Any]]:
    path = content_root / "npcs.json"
    try:
        with path.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
    except FileNotFoundError as exc:
        raise LivingWorldContentError(f"missing authored content file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LivingWorldContentError(
            f"invalid authored content JSON in {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(entries, list):
        raise LivingWorldContentError(f"{path}: root must be an array")
    result: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise LivingWorldContentError(f"{path}[{index}]: must be an object")
        npc_id = _identifier(entry.get("id"), f"{path}[{index}].id")
        if npc_id in result:
            raise LivingWorldContentError(f"{path}: duplicate NPC id {npc_id!r}")
        result[npc_id] = entry
    return result


def load_living_world_content(
    content_root: Path | None = None,
) -> LivingWorldContent:
    """Load and validate the complete authored living-world catalogue."""

    root = content_root or CONTENT_ROOT
    living_root = root / "living_world"
    return validate_living_world_content(
        world_document=_read_json(living_root / "world.json"),
        npc_document=_read_json(living_root / "npc_profiles.json"),
        rumor_document=_read_json(living_root / "rumors.json"),
        trigger_document=_read_json(living_root / "triggers.json"),
        core_npcs=_load_core_npcs(root),
    )
