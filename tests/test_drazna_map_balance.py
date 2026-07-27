"""Traversal, presentation, and encounter-budget guards for Drazna.

These checks deliberately exercise the authored maps through the same
RoomEngine primitives used by live play. They are stricter than the generic
room validator: a technically valid room can still crop its landmark art,
hide an entry under a sprite, flatten a region loop, or make its bespoke
enemies irrelevant to equipment progression.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import combinations

from backend.actor_defs import enemy_art, get_actor_art
from backend.content import load_region
from backend.entities import Disposition, NPC, Player, Position
from backend.events import EventType
from backend.inventory import attack_range, equip
from backend.models import TileType
from backend.object_defs import get_object_definition, occupied_cells
from backend.room_engine import RoomEngine
from backend.room_loader import EnemySpawn, RoomObject, RoomTemplate
from backend.seeds import (
    ENEMY_DEFS,
    LIVING_NPC_SEEDS,
    SECONDARY_AUTHORED_NPC_SEEDS,
    STARTER_ITEMS,
)


REGION = load_region("world/drazna/region.json")
ROOMS = REGION["rooms"]
RESERVED_GATEWAYS = {
    "drazna_lantern_quays": (
        (0, 6, "frontier", "The procedural road"),
        (0, 9, "connection", "The temporary Oakrun bridge"),
    ),
}
ENEMIES = {definition["id"]: definition for definition in ENEMY_DEFS}
ITEMS = {
    definition["name"]: {**definition, "id": 10_000 + index}
    for index, definition in enumerate(STARTER_ITEMS)
}
NPC_SEEDS_BY_ID = {
    persona["id"]: seed
    for seed in (*LIVING_NPC_SEEDS, *SECONDARY_AUTHORED_NPC_SEEDS)
    for persona in (seed[1],)
}


@dataclass(frozen=True)
class ExitSpec:
    position: tuple[int, int]
    kind: str
    label: str


def _room_exits(room_id: str) -> list[ExitSpec]:
    exits = [
        ExitSpec(
            (edge["x"], edge["y"]),
            "connection",
            edge["to"],
        )
        for edge in REGION["connections"]
        if edge["from"] == room_id
    ]
    exits.extend(
        ExitSpec((x, y), kind, label)
        for x, y, kind, label in RESERVED_GATEWAYS.get(room_id, ())
    )
    return exits


def _objects(room: dict) -> list[RoomObject]:
    result = []
    for index, placement in enumerate(room.get("objects", [])):
        definition = get_object_definition(placement["type"])
        assert definition is not None
        result.append(RoomObject(
            id=placement.get("id", f"object_{index + 1}"),
            type=definition.id,
            position=(placement["x"], placement["y"]),
            label=definition.label,
            description=definition.description,
            details=list(definition.details),
            footprint=definition.footprint,
            blocks_movement=definition.blocks_movement,
            image=definition.image,
            visual_size=definition.visual_size,
            interaction=definition.interaction,
        ))
    return result


def _template(room_id: str, *, include_enemies: bool = True) -> RoomTemplate:
    room = ROOMS[room_id]
    exits = _room_exits(room_id)
    connections = {
        exit_spec.position: 1_000 + index
        for index, exit_spec in enumerate(exits)
        if exit_spec.kind == "connection"
    }
    frontier_exits = {
        exit_spec.position: exit_spec.label
        for exit_spec in exits
        if exit_spec.kind == "frontier"
    }
    enemy_spawns = []
    if include_enemies:
        for spawn in room.get("enemy_spawns", []):
            definition = ENEMIES[spawn["enemy_id"]]
            enemy_spawns.append(EnemySpawn(
                name=definition["name"],
                hp=definition["hp"],
                attack_damage=definition["attack_damage"],
                defense=definition["defense"],
                position=(spawn["x"], spawn["y"]),
            ))
    return RoomTemplate(
        room_id=999,
        room_name=room["name"],
        width=room["width"],
        height=room["height"],
        spawn_points=[tuple(point) for point in room["spawn_points"]],
        walls={
            (x, y)
            for y, row in enumerate(room["terrain"])
            for x, tile in enumerate(row)
            if not TileType(tile).passable
        },
        content_id=room_id,
        enemies=enemy_spawns,
        objects=_objects(room),
        capacity=len(room["spawn_points"]),
        connections=connections,
        frontier_exits=frontier_exits,
    )


def _walkable(room: dict) -> set[tuple[int, int]]:
    result = {
        (x, y)
        for y, row in enumerate(room["terrain"])
        for x, tile in enumerate(row)
        if TileType(tile).passable
    }
    for placement in room.get("objects", []):
        definition = get_object_definition(placement["type"])
        assert definition is not None
        if definition.blocks_movement:
            result.difference_update(
                occupied_cells(
                    definition,
                    placement["x"],
                    placement["y"],
                )
            )
    return result


def _neighbors(point: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    x, y = point
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))


def _distances(
    walkable: set[tuple[int, int]],
    start: tuple[int, int],
    *,
    forbidden: set[tuple[int, int]] | None = None,
) -> dict[tuple[int, int], int]:
    forbidden = forbidden or set()
    reached = {start: 0}
    queue = deque([start])
    while queue:
        point = queue.popleft()
        for neighbor in _neighbors(point):
            if (
                neighbor in walkable
                and neighbor not in forbidden
                and neighbor not in reached
            ):
                reached[neighbor] = reached[point] + 1
                queue.append(neighbor)
    return reached


def _path(
    walkable: set[tuple[int, int]],
    start: tuple[int, int],
    target: tuple[int, int],
    *,
    forbidden: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    forbidden = (forbidden or set()) - {target}
    previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue = deque([start])
    while queue:
        point = queue.popleft()
        if point == target:
            break
        for neighbor in _neighbors(point):
            if (
                neighbor in walkable
                and neighbor not in forbidden
                and neighbor not in previous
            ):
                previous[neighbor] = point
                queue.append(neighbor)
    assert target in previous, f"{target} is unreachable from {start}"
    result = []
    point = target
    while point != start:
        result.append(point)
        parent = previous[point]
        assert parent is not None
        point = parent
    return list(reversed(result))


def _arrival(
    walkable: set[tuple[int, int]],
    exits: list[ExitSpec],
    anchor: tuple[int, int],
) -> tuple[int, int]:
    exit_tiles = {exit_spec.position for exit_spec in exits}
    return min(
        walkable - exit_tiles,
        key=lambda point: (
            abs(point[0] - anchor[0]) + abs(point[1] - anchor[1]),
            point[1],
            point[0],
        ),
    )


def _visual_bounds(room_object: RoomObject) -> tuple[float, float, float, float]:
    cells = room_object.occupied_cells()
    min_x = min(x for x, _ in cells)
    max_x = max(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    max_y = max(y for _, y in cells)
    logical_width = max_x - min_x + 1
    visual_width, visual_height = room_object.visual_size
    left = min_x + (logical_width - visual_width) / 2
    bottom = max_y + 1
    return left, bottom - visual_height, left + visual_width, bottom


def _rectangle_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    return (
        max(0, min(first[2], second[2]) - max(first[0], second[0]))
        * max(0, min(first[3], second[3]) - max(first[1], second[1]))
    )


def test_drazna_keeps_seven_real_loops_around_one_deliberate_palace_leaf():
    room_ids = set(ROOMS)
    undirected_edges = {
        tuple(sorted((edge["from"], edge["to"])))
        for edge in REGION["connections"]
    }
    assert len(room_ids) == 19
    assert len(undirected_edges) == 25
    assert len(undirected_edges) - len(room_ids) + 1 == 7

    bridges = set()
    for removed in undirected_edges:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for source, target in undirected_edges - {removed}:
            adjacency[source].add(target)
            adjacency[target].add(source)
        start = next(iter(room_ids))
        reached = {start}
        queue = deque([start])
        while queue:
            source = queue.popleft()
            for target in adjacency[source]:
                if target not in reached:
                    reached.add(target)
                    queue.append(target)
        if reached != room_ids:
            bridges.add(removed)

    assert bridges == {
        tuple(sorted((
            "drazna_high_crown",
            "drazna_palace_still_water",
        )))
    }


def test_all_fifty_two_entries_and_every_interactable_have_clear_paths():
    traversed = 0
    chest_count = 0
    carriage_count = 0
    chest_entry_distances = []
    for room_id, room in ROOMS.items():
        exits = _room_exits(room_id)
        walkable = _walkable(room)
        exit_tiles = {exit_spec.position for exit_spec in exits}

        for source_index, exit_spec in enumerate(exits):
            source = exits[(source_index + 1) % len(exits)]
            arrival = _arrival(walkable, exits, source.position)
            route = _path(
                walkable,
                arrival,
                exit_spec.position,
                forbidden=exit_tiles - {exit_spec.position},
            )

            engine = RoomEngine(_template(room_id, include_enemies=False))
            player = Player(
                id="player_audit",
                name="Route Auditor",
                position=Position(0, 0),
                hp=100,
                max_hp=100,
                defense=1,
                attack_damage=30,
            )
            engine.attach_player(player, Position(*arrival))
            events = []
            current = arrival
            for next_point in route:
                step = [
                    next_point[0] - current[0],
                    next_point[1] - current[1],
                ]
                events, resolved = engine.submit_action(
                    player.id,
                    {"action_type": "move", "direction": step},
                )
                assert resolved
                current = next_point
            expected = (
                EventType.PLAYER_ENTERED_FRONTIER
                if exit_spec.kind == "frontier"
                else EventType.PLAYER_ENTERED_DOOR
            )
            assert expected in {event.event_type for event in events}
            traversed += 1

        reached = _distances(walkable, exits[0].position)
        assert exit_tiles <= set(reached)
        for room_object in _objects(room):
            approaches = {
                neighbor
                for cell in room_object.occupied_cells()
                for neighbor in _neighbors(cell)
                if neighbor in walkable
            }
            assert approaches & set(reached), (
                f"{room_id}:{room_object.id} has no reachable interaction tile"
            )
            if room_object.type == "chest":
                chest_entry_distances.append(min(
                    min(
                        _distances(
                            walkable,
                            _arrival(walkable, exits, exit_spec.position),
                        ).get(approach, 10_000)
                        for approach in approaches
                    )
                    for exit_spec in exits
                ))
            chest_count += room_object.type == "chest"
            carriage_count += room_object.type == "drazna_mudwheel_stop"

    assert traversed == 52
    assert chest_count == 15
    assert carriage_count == 3
    assert min(chest_entry_distances) >= 4
    assert sum(distance >= 12 for distance in chest_entry_distances) >= 3


def test_landmark_art_stays_inside_the_grid_and_off_critical_footfalls():
    npc_positions: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for room_id, _persona, x, y, *_ in (
        *LIVING_NPC_SEEDS,
        *SECONDARY_AUTHORED_NPC_SEEDS,
    ):
        if room_id in ROOMS:
            npc_positions[room_id].add((x, y))

    for room_id, room in ROOMS.items():
        room_objects = _objects(room)
        critical = {
            *[exit_spec.position for exit_spec in _room_exits(room_id)],
            *map(tuple, room["spawn_points"]),
            *npc_positions[room_id],
            *[
                (spawn["x"], spawn["y"])
                for spawn in room.get("enemy_spawns", [])
            ],
        }
        bounds_by_id = {
            room_object.id: _visual_bounds(room_object)
            for room_object in room_objects
        }
        for room_object in room_objects:
            left, top, right, bottom = bounds_by_id[room_object.id]
            assert 0 <= left < right <= room["width"], (
                f"{room_id}:{room_object.id} spills horizontally"
            )
            assert 0 <= top < bottom <= room["height"], (
                f"{room_id}:{room_object.id} is cropped vertically"
            )

            logical = set(room_object.occupied_cells())
            max_logical_y = max(y for _, y in logical)
            covered_footfalls = {
                (x, y)
                for x, y in critical - logical
                if left < x + 0.5 < right
                and top < y + 0.8
                and y <= max_logical_y
            }
            assert not covered_footfalls, (
                f"{room_id}:{room_object.id} hides {covered_footfalls}"
            )

        for first, second in combinations(room_objects, 2):
            assert _rectangle_overlap(
                bounds_by_id[first.id],
                bounds_by_id[second.id],
            ) == 0, f"{room_id}:{first.id} visually overlaps {second.id}"


def test_every_room_has_an_illustrated_anchor_and_core_evidence_has_art():
    """Dense evidence rooms should not collapse into a field of sparkle icons."""
    for room_id, room in ROOMS.items():
        illustrated = [
            room_object
            for room_object in _objects(room)
            if room_object.image is not None
        ]
        assert illustrated, f"{room_id} has no illustrated visual anchor"

    for object_type in {
        "crown_ledger_plinth",
        "first_rot_memorial",
        "drazna_false_manifest",
        "drazna_omitted_tablets",
        "drazna_crown_flood_order",
        "drazna_preproclamation_roll",
        "drazna_barge_plaque",
        "drazna_sluice_tools",
        "drazna_pressure_gauge",
        "drazna_black_key_hook",
    }:
        definition = get_object_definition(object_type)
        assert definition is not None
        assert definition.image is not None, (
            f"{object_type} lost its evidence-specific presentation"
        )


def test_every_drazna_actor_resolves_to_world_art():
    drazna_npcs = [
        persona
        for room_id, persona, *_ in (
            *LIVING_NPC_SEEDS,
            *SECONDARY_AUTHORED_NPC_SEEDS,
        )
        if room_id in ROOMS
    ]
    assert drazna_npcs
    assert all(get_actor_art(persona.get("art_id")) for persona in drazna_npcs)
    assert NPC_SEEDS_BY_ID["lina-pell"][1]["art_id"] == "carriage_driver"

    regional_enemy_ids = {
        spawn["enemy_id"]
        for room in ROOMS.values()
        for spawn in room.get("enemy_spawns", [])
    }
    assert regional_enemy_ids
    assert all(enemy_art(ENEMIES[enemy_id]["name"]) for enemy_id in regional_enemy_ids)


def test_every_mudwheel_stop_has_party_space_and_a_short_route_from_each_entry():
    stop_rooms = {}
    for room_id, room in ROOMS.items():
        stops = [
            room_object
            for room_object in _objects(room)
            if room_object.type == "drazna_mudwheel_stop"
        ]
        if stops:
            assert len(stops) == 1
            stop_rooms[room_id] = stops[0]

    assert set(stop_rooms) == {
        "drazna_lantern_quays",
        "drazna_high_crown",
        "drazna_birch_heights",
    }
    for room_id, stop in stop_rooms.items():
        room = ROOMS[room_id]
        exits = _room_exits(room_id)
        walkable = _walkable(room)
        approaches = {
            neighbor
            for cell in stop.occupied_cells()
            for neighbor in _neighbors(cell)
            if neighbor in walkable
        }
        # Player plus the maximum three followers can gather around the stop.
        assert len(approaches) >= 4
        for exit_spec in exits:
            arrival = _arrival(walkable, exits, exit_spec.position)
            distances = _distances(walkable, arrival)
            assert min(distances.get(point, 10_000) for point in approaches) <= 17


def test_drazna_enemy_durability_rewards_a_real_weapon_upgrade():
    """Each bespoke regional enemy takes two bare hits but one steel hit."""
    for enemy_id in (13, 14, 15, 16):
        enemy = ENEMIES[enemy_id]
        bare_damage = max(1, 30 - enemy["defense"])
        steel_damage = max(1, 42 - enemy["defense"])
        assert bare_damage < enemy["hp"] <= steel_damage


def test_enemy_clusters_warn_every_entry_before_contact_but_still_guard_a_route():
    for room_id, room in ROOMS.items():
        hostile_positions = {
            (spawn["x"], spawn["y"])
            for spawn in room.get("enemy_spawns", [])
        }
        if room_id == "drazna_gate_seven":
            hostile_positions.add((8, 7))
        if not hostile_positions:
            continue

        walkable = _walkable(room)
        exits = _room_exits(room_id)
        nearest_by_entry = []
        for exit_spec in exits:
            arrival = _arrival(walkable, exits, exit_spec.position)
            nearest_by_entry.append(min(
                abs(arrival[0] - hostile[0]) + abs(arrival[1] - hostile[1])
                for hostile in hostile_positions
            ))
        # No traveller is struck on the first orientation step.
        assert min(nearest_by_entry) >= 4
        # Every combat room has at least one deliberately guarded approach.
        assert min(nearest_by_entry) <= 6


def _hostiles(engine: RoomEngine):
    return [
        *engine.room.living_enemies(),
        *[
            npc
            for npc in engine.room.living_npcs()
            if npc.disposition is Disposition.HOSTILE
        ],
    ]


def _combat_action(engine: RoomEngine, player: Player) -> dict:
    hostiles = _hostiles(engine)
    reach = attack_range(player)

    def distance(actor) -> int:
        return (
            abs(player.position.x - actor.position.x)
            + abs(player.position.y - actor.position.y)
        )

    targets = [actor for actor in hostiles if distance(actor) <= reach]
    if targets:
        target = min(
            targets,
            key=lambda actor: (actor.hp, distance(actor), actor.id),
        )
        return {"action_type": "attack", "target_id": target.id}

    start = (player.position.x, player.position.y)
    previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue = deque([start])
    goal = None
    while queue and goal is None:
        point = queue.popleft()
        if any(
            abs(point[0] - actor.position.x)
            + abs(point[1] - actor.position.y)
            <= reach
            for actor in hostiles
        ):
            goal = point
            break
        for neighbor in _neighbors(point):
            occupant_id = (
                engine.room.is_occupied(*neighbor)
                if engine.room.is_valid_position(*neighbor)
                else None
            )
            occupant = (
                engine.room.get_entity(occupant_id)
                if occupant_id is not None
                else None
            )
            if (
                neighbor in previous
                or not engine.room.is_valid_position(*neighbor)
                or (
                    occupant_id is not None
                    and not (
                        isinstance(occupant, NPC)
                        and occupant.disposition is not Disposition.HOSTILE
                    )
                )
            ):
                continue
            previous[neighbor] = point
            queue.append(neighbor)

    assert goal is not None
    while previous[goal] != start:
        parent = previous[goal]
        assert parent is not None
        goal = parent
    return {
        "action_type": "move",
        "direction": [goal[0] - start[0], goal[1] - start[1]],
    }


def _add_odran(engine: RoomEngine) -> None:
    room_id, persona, x, y, hp, defense, attack_damage = next(
        seed
        for seed in SECONDARY_AUTHORED_NPC_SEEDS
        if seed[1]["id"] == "odran-third-bell"
    )
    assert room_id == "drazna_gate_seven"
    engine.room.add_npc(NPC(
        id="npc_odran",
        db_id=8_888,
        name=persona["name"],
        position=Position(x, y),
        hp=hp,
        max_hp=hp,
        defense=defense,
        attack_damage=attack_damage,
        disposition=Disposition.HOSTILE,
        persona=persona,
    ))
    engine.refresh_mode()


def _run_encounter(
    room_id: str,
    entry_index: int,
    *,
    hp: int = 100,
    gear: tuple[str, ...] = (),
    follower_id: str | None = None,
) -> tuple[Player, int, NPC | None]:
    engine = RoomEngine(_template(room_id))
    if room_id == "drazna_gate_seven":
        _add_odran(engine)
    player = Player(
        id="player_audit",
        name="Combat Auditor",
        position=Position(0, 0),
        hp=hp,
        max_hp=100,
        defense=1,
        attack_damage=30,
    )
    for item_name in gear:
        player.inventory.append({
            "item": ITEMS[item_name],
            "quantity": 1,
            "equipped": False,
        })
        assert equip(player, len(player.inventory) - 1) is None

    arrival = engine.room.free_arrival(1_000 + entry_index)
    assert arrival is not None
    engine.attach_player(player, Position(*arrival))

    follower = None
    if follower_id is not None:
        (
            _room_id,
            persona,
            _x,
            _y,
            follower_hp,
            follower_defense,
            follower_attack,
        ) = NPC_SEEDS_BY_ID[follower_id]
        exit_tiles = set(engine.room.template.connections)
        follower_position = min(
            (
                (x, y)
                for y in range(engine.room.template.height)
                for x in range(engine.room.template.width)
                if engine.room.is_valid_position(x, y)
                and not engine.room.is_occupied(x, y)
                and (x, y) not in exit_tiles
            ),
            key=lambda point: (
                abs(point[0] - arrival[0]) + abs(point[1] - arrival[1]),
                point[1],
                point[0],
            ),
        )
        follower = NPC(
            id=f"npc_{follower_id}",
            db_id=9_999,
            name=persona["name"],
            position=Position(*follower_position),
            hp=follower_hp,
            max_hp=follower_hp,
            defense=follower_defense,
            attack_damage=follower_attack,
            disposition=Disposition.FRIENDLY,
            persona=persona,
            party_owner_id=player.id,
        )
        engine.room.add_npc(follower)
        engine.refresh_mode()

    rounds = 0
    while player.is_alive and _hostiles(engine) and rounds < 40:
        _events, resolved = engine.submit_action(
            player.id,
            _combat_action(engine, player),
        )
        assert resolved
        rounds += 1
    assert rounds < 40
    return player, rounds, follower


def test_every_combat_entry_is_survivable_but_gate_seven_stays_a_climax():
    ordinary_rooms = [
        room_id
        for room_id, room in ROOMS.items()
        if room.get("enemy_spawns") and room_id != "drazna_gate_seven"
    ]
    ordinary_results = [
        _run_encounter(room_id, entry_index)
        for room_id in ordinary_rooms
        for entry_index, _exit in enumerate(_room_exits(room_id))
    ]
    assert ordinary_results
    assert all(player.is_alive for player, _rounds, _follower in ordinary_results)
    assert all(
        80 <= player.hp < 100
        for player, _rounds, _follower in ordinary_results
    )

    bare = [
        _run_encounter("drazna_gate_seven", entry_index)
        for entry_index in range(2)
    ]
    common = [
        _run_encounter(
            "drazna_gate_seven",
            entry_index,
            gear=("Rusty Dagger", "Leather Cap", "Wooden Buckler"),
        )
        for entry_index in range(2)
    ]
    later = [
        _run_encounter(
            "drazna_gate_seven",
            entry_index,
            gear=("Steel Sword", "Wooden Buckler"),
        )
        for entry_index in range(2)
    ]
    assert all(player.is_alive for player, _rounds, _follower in bare)
    assert all(30 <= player.hp <= 55 for player, _rounds, _follower in bare)
    assert min(player.hp for player, _rounds, _follower in common) > max(
        player.hp for player, _rounds, _follower in bare
    )
    assert min(player.hp for player, _rounds, _follower in later) > max(
        player.hp for player, _rounds, _follower in common
    )

    wounded = [
        _run_encounter("drazna_gate_seven", entry_index, hp=50)
        for entry_index in range(2)
    ]
    assert any(
        not player.is_alive
        for player, _rounds, _follower in wounded
    )

    escorted = [
        _run_encounter(
            "drazna_gate_seven",
            entry_index,
            gear=("Rusty Dagger", "Wooden Buckler"),
            follower_id="sima-dren",
        )
        for entry_index in range(2)
    ]
    assert all(
        player.is_alive and follower is not None and follower.is_alive
        for player, _rounds, follower in escorted
    )
    assert all(
        follower is not None and 15 <= follower.hp < follower.max_hp
        for _player, _rounds, follower in escorted
    )
