from collections import defaultdict, deque

from backend.content import load_catalog, load_region
from backend.living_world_content import load_living_world_content
from backend.room_validation import (
    validate_connection,
    validate_enemy_refs,
    validate_npc_placement,
    validate_room,
)
from backend.seeds import (
    ENEMY_DEFS,
    LIVING_NPC_SEEDS,
    SECONDARY_AUTHORED_NPC_SEEDS,
)
from backend.situation_defs import get_situation


DEDICATED_DRAZNA_ROOMS = {
    "drazna_lantern_quays",
    "drazna_eel_and_ember",
    "drazna_reed_market",
    "drazna_mud_crown",
    "drazna_low_lantern_den",
    "drazna_walking_ward",
    "drazna_roofwright_loft",
    "drazna_birch_stair",
    "drazna_birch_heights",
    "drazna_house_of_names",
    "drazna_tablet_vault",
    "drazna_high_crown",
    "drazna_palace_still_water",
    "drazna_crown_sluice",
    "drazna_pressure_gallery",
    "drazna_first_scar",
    "drazna_undertide",
    "drazna_dry_dock",
    "drazna_gate_seven",
}
RESERVED_DRAZNA_GATEWAYS = {
    ("drazna_lantern_quays", 0, 6),
    ("drazna_lantern_quays", 0, 9),
}


def _reachable(adjacency, start):
    seen = {start}
    queue = deque([start])
    while queue:
        room_id = queue.popleft()
        for target_id in adjacency[room_id]:
            if target_id not in seen:
                seen.add(target_id)
                queue.append(target_id)
    return seen


def test_drazna_is_a_valid_strongly_connected_nineteen_room_region():
    region = load_region("world/drazna/region.json")
    assert set(region["rooms"]) == DEDICATED_DRAZNA_ROOMS
    assert len(region["rooms"]) == 19

    known_enemy_ids = {enemy["id"] for enemy in ENEMY_DEFS}
    edges = {(edge["from"], edge["to"]) for edge in region["connections"]}
    adjacency = defaultdict(set)
    door_targets = set()
    for edge in region["connections"]:
        adjacency[edge["from"]].add(edge["to"])
        door_targets.add((edge["from"], edge["x"], edge["y"]))
        validate_connection(
            region["rooms"][edge["from"]],
            {"from_x": edge["x"], "from_y": edge["y"]},
        )
    for room in region["rooms"].values():
        validate_room(room)
        validate_enemy_refs(room, known_enemy_ids)

    assert all((target, source) in edges for source, target in edges)
    assert _reachable(adjacency, region["start_room"]) == DEDICATED_DRAZNA_ROOMS
    assert len(door_targets) == len(region["connections"])
    authored_doors = {
        (room_id, x, y)
        for room_id, room in region["rooms"].items()
        for y, row in enumerate(room["terrain"])
        for x, tile in enumerate(row)
        if tile in {"+", "@"}
    }
    # Every exit is connected exactly once except the procedural-frontier
    # gateway and temporary Oakrun bridge, which are deliberately wired by
    # their owning runtime systems.
    assert authored_doors == door_targets | RESERVED_DRAZNA_GATEWAYS
    assert door_targets.isdisjoint(RESERVED_DRAZNA_GATEWAYS)
    for room_id, x, y in authored_doors:
        room = region["rooms"][room_id]
        interior_neighbors = {
            (neighbor_x, neighbor_y)
            for neighbor_x, neighbor_y in (
                (x - 1, y),
                (x + 1, y),
                (x, y - 1),
                (x, y + 1),
            )
            if 0 <= neighbor_x < room["width"]
            and 0 <= neighbor_y < room["height"]
            and room["terrain"][neighbor_y][neighbor_x] in {".", "+", "@"}
        }
        assert interior_neighbors, f"{room_id} exit {(x, y)} has no interior approach"


def test_all_fifteen_drazna_residents_have_clear_unique_starting_tiles():
    region = load_region("world/drazna/region.json")
    placements = {}
    seeds = (*LIVING_NPC_SEEDS, *SECONDARY_AUTHORED_NPC_SEEDS)
    for room_id, persona, x, y, *_ in seeds:
        if room_id not in region["rooms"]:
            continue
        validate_npc_placement(region["rooms"][room_id], x, y)
        key = (room_id, x, y)
        assert key not in placements, (
            f"{persona['id']} overlaps {placements.get(key)} at {key}"
        )
        placements[key] = persona["id"]

    assert set(placements.values()) == {
        "mara-vey",
        "alin-vey",
        "ilya-sorn",
        "nera-bell",
        "olek-var",
        "pava-mirek",
        "vasko-mirek",
        "vesna-korr",
        "lina-pell",
        "drina-sable",
        "teo-latch",
        "rada-velic",
        "sima-dren",
        "luka-nen",
        "odran-third-bell",
    }


def test_all_drazna_dialogue_personas_share_one_intertwined_relationship_web():
    region = load_region("world/drazna/region.json")
    personas = {
        persona["id"]: persona
        for room_id, persona, *_ in (
            *LIVING_NPC_SEEDS,
            *SECONDARY_AUTHORED_NPC_SEEDS,
        )
        if room_id in region["rooms"]
    }
    assert len(personas) == 15

    adjacency = defaultdict(set)
    directed_edges = set()
    for npc_id, persona in personas.items():
        relationships = persona["relationships"]
        assert relationships, f"{npc_id} has no dialogue relationship context"
        targets = [relationship["npc_id"] for relationship in relationships]
        assert len(targets) == len(set(targets))
        assert npc_id not in targets
        assert set(targets) <= set(personas)
        for target_id in targets:
            directed_edges.add((npc_id, target_id))
            adjacency[npc_id].add(target_id)
            adjacency[target_id].add(npc_id)

    assert _reachable(adjacency, "mara-vey") == set(personas)
    # Family, palace/archive politics, and the Low Lantern information market
    # must all exist in the same dialogue graph as the simulated consequences.
    assert {
        ("mara-vey", "alin-vey"),
        ("pava-mirek", "vasko-mirek"),
        ("nera-bell", "luka-nen"),
        ("olek-var", "teo-latch"),
        ("vesna-korr", "teo-latch"),
        ("lina-pell", "drina-sable"),
    } <= directed_edges


def test_drazna_has_dedicated_landmarks_services_and_enemy_ecology():
    region = load_region("world/drazna/region.json")
    buildings = load_catalog("buildings.json")
    objects = load_catalog("objects.json")
    boards = load_catalog("noticeboards.json")
    shops = load_catalog("shops.json")

    landmark_ids = {
        "palace_of_still_water",
        "crown_sluice_gatehouse",
        "drowned_bell_tower",
        "house_of_names",
        "eel_and_ember_inn",
        "walking_bridge_houses",
        "dry_dock_entrance",
        "birch_stair_memorial_arch",
    }
    art_object_ids = {
        "drazna_ferry_skiff",
        "amber_quay_stall",
        "sluice_control_wheel",
        "house_of_names_rack",
        "roofwright_scaffold",
        "salvage_crane",
        "low_lantern_cache",
        "floodline_memorial",
        "crown_ledger_plinth",
        "first_rot_memorial",
    }
    assert landmark_ids <= set(buildings)
    assert art_object_ids <= set(objects)
    assert all(buildings[item]["image"].startswith("/art/world/") for item in landmark_ids)
    assert all(objects[item]["image"].startswith("/art/world/") for item in art_object_ids)

    placed_objects = {
        placement["id"]
        for room in region["rooms"].values()
        for placement in room.get("objects", [])
    }
    assert boards["drazna_lantern_quays_board"]["object_id"] in placed_objects
    assert shops["drazna_amber_quay_provisions"]["object_id"] in placed_objects
    assert "drazna_gate_black_key" in placed_objects
    assert objects["drazna_black_key_hook"]["discovery"]["key"] == (
        "drazna:odrans-black-key"
    )
    first_record = objects["first_rot_memorial"]["discovery"]
    assert first_record["key"] == "drazna:first-public-record"
    assert "verified public record" in first_record["summary"].lower()
    assert "unknown beginning" in first_record["summary"].lower()

    enemy_ids = {
        spawn["enemy_id"]
        for room in region["rooms"].values()
        for spawn in room.get("enemy_spawns", [])
    }
    assert {13, 14, 15, 16} <= enemy_ids


def test_drazna_people_and_simulation_locations_match_the_physical_region():
    profiles = load_living_world_content().npc_profiles
    core_npcs = load_catalog("npcs.json")
    expected_core = {
        "drina-sable",
        "teo-latch",
        "rada-velic",
        "sima-dren",
        "luka-nen",
        "odran-third-bell",
    }
    assert expected_core <= set(core_npcs)
    assert expected_core <= set(profiles)
    assert all(
        3 <= len(profiles[npc_id]["deliberation_windows"]) <= 6
        for npc_id in expected_core
    )

    drazna_schedule_locations = {
        anchor["location_id"]
        for profile in profiles.values()
        for anchor in profile["schedule"]
        if anchor["location_id"].startswith("drazna_")
    }
    assert drazna_schedule_locations <= DEDICATED_DRAZNA_ROOMS

    odran = core_npcs["odran-third-bell"]
    assert odran["spawn"]["room"] == "drazna_gate_seven"
    assert odran["disposition"] == "hostile"
    assert all(
        enemy["name"] != "Odran, the Sluicebound"
        for enemy in ENEMY_DEFS
    )


def test_drazna_story_web_has_missable_consequences_and_multiple_gate_outcomes():
    content = load_living_world_content()
    required = {
        "teo-sells-low-lantern-list",
        "sima-bridge-injury",
        "undertide-expedition-launch",
        "vasko-returns-with-ledger",
        "nera-tablet-theft",
        "mara-alin-hearing-outcome",
        "gate-seven-climax",
        "gate-seven-unanswered-flood",
        "odran-cadence-pacified",
        "gate-seven-pacified-aftermath",
        "gate-seven-cadence-expired",
        "odran-falls-gate-held",
        "gate-seven-killed-aftermath",
        "gate-seven-contained-aftermath",
        "gate-seven-flood-aftermath",
        "luka-dry-dock-last-window",
        "luka-dry-dock-survives-window",
    }
    assert required <= set(content.triggers)

    non_expiring_resolution_triggers = {
        "gate-seven-climax",
        "gate-seven-unanswered-flood",
        "odran-cadence-pacified",
        "gate-seven-pacified-aftermath",
        "gate-seven-cadence-expired",
        "odran-falls-gate-held",
        "gate-seven-killed-aftermath",
        "gate-seven-contained-aftermath",
        "gate-seven-flood-aftermath",
        "luka-dry-dock-last-window",
        "luka-dry-dock-survives-window",
    }
    for trigger_id in non_expiring_resolution_triggers:
        trigger = content.triggers[trigger_id]
        assert trigger["window"]["closes_day"] is None
        assert trigger["missed_consequences"] == []
        assert trigger["aftermath_clues"] == []

    finite = [
        content.triggers[trigger_id]
        for trigger_id in required - non_expiring_resolution_triggers
    ]
    assert all(
        trigger["window"]["closes_day"] is not None for trigger in finite
    )
    assert all(trigger["missed_consequences"] for trigger in finite)

    effects = [
        effect
        for trigger_id in required
        for trigger in (content.triggers[trigger_id],)
        for effect in (*trigger["effects"], *trigger["missed_consequences"])
    ]
    assert {
        "claim_fact",
        "wound_npc",
        "kill_npc",
        "set_fact",
        "set_goal_status",
    } <= {
        effect["kind"] for effect in effects
    }
    resolution_values = {
        effect["value"]["state"]
        for effect in effects
        if effect["kind"] in {"claim_fact", "set_fact"}
        and effect["fact_key"] == "drazna.gate_seven_resolution"
    }
    resolution_values |= {
        condition["value"]["state"]
        for trigger_id in required
        for trigger in (content.triggers[trigger_id],)
        for condition in trigger["conditions"]
        if condition["kind"] == "fact_equals"
        and condition["fact_key"] == "drazna.gate_seven_resolution"
    }
    assert {
        "pacified",
        "contained",
        "odran-killed",
        "flooded",
    } <= resolution_values
    assert "cadence-failed" not in resolution_values

    failure_facts = {
        trigger_id: {
            effect["fact_key"]: effect["value"]
            for effect in content.triggers[trigger_id]["effects"]
            if effect["kind"] in {"claim_fact", "set_fact"}
        }
        for trigger_id in {
            "gate-seven-unanswered-flood",
            "gate-seven-cadence-expired",
        }
    }
    assert all(
        facts["drazna.gate_seven_climax"]["state"] == "flooded"
        for facts in failure_facts.values()
    )


def test_first_scar_evidence_preserves_record_without_claiming_origin():
    content = load_living_world_content()
    drazna = content.kingdoms["drazna"]
    assert drazna["first_public_rot_record"] is True
    assert "not a proven birthplace" in drazna["public_account"]

    competing_accounts = {
        "first-scar-salt-barge",
        "first-scar-palace-drain",
    }
    for rumor_id in competing_accounts:
        truth = content.rumors[rumor_id]["truth"]
        assert truth["classification"] in {"partial", "unresolved"}
        assert "source" not in truth["account"].lower() or "not" in truth["account"].lower()

    gate = content.rumors["gate-seven-fourteen"]["truth"]["account"]
    assert "do not explain the rot's origin" in gate


def test_gate_seven_situation_text_matches_its_durable_fact_shapes():
    situation = get_situation("drazna-gate-seven-reckoning")
    assert situation is not None
    choices = {choice.id: choice for choice in situation.choices}

    assert situation.defeat_outcome.fact_value == {
        "state": "odran-killed",
        "gate": "held-by-chain",
        "names_spoken": 0,
    }
    assert "emergency notch" in situation.defeat_outcome.result
    assert "remains open" not in situation.defeat_outcome.result
    assert choices["answer-the-fourteenth"].fact_value == {
        "state": "pacified",
        "gate": "vented",
        "names_spoken": 14,
    }
    assert choices["brace-the-counterpressure"].fact_value == {
        "state": "contained",
        "gate": "braced",
        "names_spoken": 0,
    }
