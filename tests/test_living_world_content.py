import copy
import json
from pathlib import Path

import pytest

from backend.living_world_content import (
    LivingWorldContentError,
    load_living_world_content,
    validate_living_world_content,
)


CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content"


def _raw_documents():
    living_root = CONTENT_ROOT / "living_world"

    def read(name):
        with (living_root / name).open("r", encoding="utf-8") as handle:
            return json.load(handle)

    with (CONTENT_ROOT / "npcs.json").open("r", encoding="utf-8") as handle:
        core_entries = json.load(handle)
    return {
        "world_document": read("world.json"),
        "npc_document": read("npc_profiles.json"),
        "rumor_document": read("rumors.json"),
        "trigger_document": read("triggers.json"),
        "core_npcs": {entry["id"]: entry for entry in core_entries},
    }


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def test_living_world_catalogue_loads_as_one_cross_referenced_unit():
    content = load_living_world_content()

    assert set(content.kingdoms) == {"amberfall", "drazna", "rouvray"}
    assert len(content.locations) == 34
    assert len(content.routes) == 43
    assert len(content.hostile_passages) == 9
    assert len(content.carriages) == 4
    assert len(content.npc_profiles) == 28
    assert len(content.rumors) == 15
    assert len(content.triggers) == 41


def test_all_core_npcs_have_profiles_and_new_people_are_natural_world_residents():
    content = load_living_world_content()
    with (CONTENT_ROOT / "npcs.json").open("r", encoding="utf-8") as handle:
        core = {entry["id"] for entry in json.load(handle)}

    assert len(core) == 16
    assert core <= set(content.npc_profiles)
    assert {
        "mara-vey",
        "ilya-sorn",
        "nera-bell",
        "olek-var",
        "pava-mirek",
        "vasko-mirek",
        "vesna-korr",
        "alin-vey",
        "jory-rusk",
        "sabine-vauclair",
        "matthieu-orne",
        "lina-pell",
    } == set(content.npc_profiles) - core


def test_every_npc_deliberates_only_three_to_six_times_per_day():
    content = load_living_world_content()

    for profile in content.npc_profiles.values():
        windows = profile["deliberation_windows"]
        assert 3 <= len(windows) <= 6
        minutes = [window["minute"] for window in windows]
        assert minutes == sorted(set(minutes))
        assert all(0 <= minute < 1440 for minute in minutes)
        assert len(profile["schedule"]) >= 3
        assert profile["needs"]
        assert profile["private_goals"]


def test_thought_content_gives_directions_but_not_tile_level_movement():
    content = load_living_world_content()
    story_effects = [
        effect
        for trigger in content.triggers.values()
        for effect in (*trigger["effects"], *trigger["missed_consequences"])
    ]

    assert any(effect["kind"] == "set_direction" for effect in story_effects)
    assert all("x" not in effect and "y" not in effect for effect in story_effects)
    assert all(
        set(profile["movement"])
        == {
            "travel_modes",
            "maximum_route_risk",
            "avoid_threats",
            "fallback_location_id",
        }
        for profile in content.npc_profiles.values()
    )


def test_content_has_no_tracked_task_or_state_shape():
    documents = _raw_documents()
    forbidden = {
        "objective",
        "objectives",
        "quest",
        "quests",
        "quest_id",
        "quest_state",
    }

    for name in (
        "world_document",
        "npc_document",
        "rumor_document",
        "trigger_document",
    ):
        assert forbidden.isdisjoint(key.lower() for key in _all_keys(documents[name]))


def test_drazna_is_first_verified_record_not_asserted_origin():
    content = load_living_world_content()
    first = [
        kingdom
        for kingdom in content.kingdoms.values()
        if kingdom["first_public_rot_record"]
    ]

    assert [kingdom["id"] for kingdom in first] == ["drazna"]
    assert "not a proven birthplace" in first[0]["public_account"]
    rumor = content.rumors["drazna-first-record"]
    assert rumor["truth"]["classification"] == "partial"
    assert "not where" in rumor["truth"]["account"]


def test_rumors_keep_truth_belief_and_source_separate():
    content = load_living_world_content()

    for rumor in content.rumors.values():
        assert set(rumor["truth"]) == {"classification", "account", "evidence"}
        assert rumor["beliefs"]
        for belief in rumor["beliefs"]:
            assert belief["claim"]
            assert 0 <= belief["confidence"] <= 100
            assert set(belief["source"]) == {"kind", "ref", "chain"}
            profile = content.npc_profiles[belief["holder_npc_id"]]
            assert {
                "rumor_id": rumor["id"],
                "belief_id": belief["id"],
            } in profile["belief_refs"]


def test_conversations_can_chain_without_increasing_deliberation_frequency():
    content = load_living_world_content()
    conversations = [
        trigger
        for trigger in content.triggers.values()
        if trigger["kind"] == "conversation"
    ]

    assert len(conversations) >= 8
    assert any(
        trigger["conversation"]["mode"] == "continuous"
        and trigger["conversation"]["max_turns"] >= 6
        for trigger in conversations
    )
    assert any(
        trigger["conversation"]["followup_trigger_ids"]
        for trigger in conversations
    )


def test_finite_offscreen_opportunities_are_final_but_leave_aftermath():
    content = load_living_world_content()
    finite = [
        trigger
        for trigger in content.triggers.values()
        if trigger["kind"] == "story"
        and trigger["window"]["closes_day"] is not None
    ]

    assert len(finite) >= 5
    assert all(trigger["missed_consequences"] for trigger in finite)
    assert all(trigger["aftermath_clues"] for trigger in finite)
    assert all(
        profile["offscreen_policy"]["missed_windows_are_final"]
        for profile in content.npc_profiles.values()
    )
    assert all(
        not profile["offscreen_policy"]["can_die"]
        or profile["offscreen_policy"]["minimum_warning_memories"] >= 1
        for profile in content.npc_profiles.values()
    )


def test_three_kingdoms_are_connected_by_hostile_roads_and_public_carriages():
    content = load_living_world_content()
    carriage_edges = set()
    for carriage in content.carriages.values():
        first = content.locations[carriage["stop_location_ids"][0]]["kingdom_id"]
        last = content.locations[carriage["stop_location_ids"][-1]]["kingdom_id"]
        if first != last:
            carriage_edges.add(frozenset((first, last)))

    assert frozenset(("amberfall", "drazna")) in carriage_edges
    assert frozenset(("amberfall", "rouvray")) in carriage_edges
    assert all(
        passage["route_id"] in content.routes
        and passage["threats"]
        and passage["warning"]
        for passage in content.hostile_passages.values()
    )
    assert {
        content.locations[route["from_location_id"]]["kingdom_id"]
        for route in content.routes.values()
    } >= {"amberfall", "drazna", "rouvray"}


def test_carriages_have_stops_hours_travel_time_and_frontier_eligibility():
    content = load_living_world_content()
    network = content.carriage_network

    assert network["scope"] == "shared_world"
    assert network["network_visibility"] == "public"
    frontier = network["generated_frontier"]
    assert set(frontier["eligible_biomes"]) == {
        "amberfall_fields",
        "drazna_marches",
        "rouvray_lowlands",
        "deep_frontier",
    }
    assert frontier["first_arrival_may_name"] is True
    assert frontier["requires_arrival_before_naming"] is True
    assert frontier["default_stop_name"] == "Unnamed Waystop"
    assert frontier["name_character_limit"] == 32
    assert frontier["generated_departure_minutes"] == [360, 720, 1080]
    assert any(
        carriage["serves_generated_frontier"]
        for carriage in content.carriages.values()
    )

    for carriage in content.carriages.values():
        assert len(carriage["stop_location_ids"]) == len(carriage["route_ids"]) + 1
        assert carriage["operating_windows"]
        for route_id in carriage["route_ids"]:
            assert content.routes[route_id]["travel_minutes"] > 0
        for departure in carriage["departures"]:
            assert any(
                window["day"] == departure["day"]
                and window["from_location_id"] == departure["from_location_id"]
                and window["start_minute"]
                <= departure["minute"]
                <= window["end_minute"]
                for window in carriage["operating_windows"]
            )


def test_unknown_fields_and_executable_vocabulary_are_rejected():
    documents = _raw_documents()
    documents["world_document"]["routes"][0]["teleport"] = True
    with pytest.raises(LivingWorldContentError, match="unknown properties: teleport"):
        validate_living_world_content(**documents)

    documents = _raw_documents()
    documents["trigger_document"]["triggers"][0]["effects"][0]["kind"] = "invent_plot"
    with pytest.raises(LivingWorldContentError, match="must be one of"):
        validate_living_world_content(**documents)


def test_consequence_conditions_and_effects_use_closed_validated_schemas():
    documents = _raw_documents()
    trigger = documents["trigger_document"]["triggers"][0]
    trigger["conditions"] = [
        {
            "kind": "npc_alive",
            "npc_id": "rowan-oakrun-courier",
            "value": True,
        },
        {"kind": "fact_absent", "fact_key": "rowan-publication"},
    ]
    trigger["effects"] = [
        {
            "kind": "set_fact",
            "fact_key": "rowan-publication",
            "subject_id": "rowan-oakrun-courier",
            "predicate": "publication",
            "value": {"state": "dated"},
        },
        {
            "kind": "set_disposition",
            "npc_id": "rowan-oakrun-courier",
            "disposition": "friendly",
        },
        {
            "kind": "set_goal_status",
            "npc_id": "rowan-oakrun-courier",
            "goal_id": "date-first-record",
            "status": "completed",
            "reason": "Rowan dated the public copy without naming a source.",
        },
    ]

    validate_living_world_content(**documents)

    trigger["effects"][-1]["goal_id"] = "invented-goal"
    with pytest.raises(LivingWorldContentError, match="unknown private goal"):
        validate_living_world_content(**documents)


def test_tracked_state_fields_are_rejected_even_before_shape_validation():
    documents = _raw_documents()
    documents["npc_document"]["profiles"][0]["status"] = "waiting"

    with pytest.raises(
        LivingWorldContentError,
        match="tracked quest/objective state is not allowed",
    ):
        validate_living_world_content(**documents)


def test_invalid_deliberation_source_and_trigger_references_are_rejected():
    documents = _raw_documents()
    documents["npc_document"]["profiles"][0]["deliberation_windows"] = [
        {"minute": 300, "purpose": "work"},
        {"minute": 600, "purpose": "replan"},
    ]
    with pytest.raises(LivingWorldContentError, match="at least 3"):
        validate_living_world_content(**documents)

    documents = _raw_documents()
    belief = documents["rumor_document"]["rumors"][0]["beliefs"][0]
    belief["source"] = {
        "kind": "npc",
        "ref": "not-a-person",
        "chain": ["not-a-person"],
    }
    with pytest.raises(LivingWorldContentError, match="unknown NPC"):
        validate_living_world_content(**documents)

    documents = _raw_documents()
    documents["trigger_document"]["triggers"][0]["participants"][0] = "not-a-person"
    with pytest.raises(LivingWorldContentError, match="unknown NPC"):
        validate_living_world_content(**documents)


def test_validator_rejects_a_private_or_unnamable_generated_network():
    documents = _raw_documents()
    documents["world_document"]["carriage_network"]["network_visibility"] = "private"
    with pytest.raises(LivingWorldContentError, match="must equal 'public'"):
        validate_living_world_content(**documents)

    documents = _raw_documents()
    policy = documents["world_document"]["carriage_network"]["generated_frontier"]
    policy["first_arrival_may_name"] = False
    with pytest.raises(LivingWorldContentError, match="must be true"):
        validate_living_world_content(**documents)


def test_loader_reports_missing_or_invalid_documents(tmp_path):
    (tmp_path / "npcs.json").write_text("[]", encoding="utf-8")
    living = tmp_path / "living_world"
    living.mkdir()
    (living / "world.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(LivingWorldContentError, match="invalid authored content JSON"):
        load_living_world_content(tmp_path)
