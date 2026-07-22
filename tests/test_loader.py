"""Tests for loading a DB room into the runtime room (issue #21)."""
import pytest
from sqlalchemy import func, select

from backend.room_engine import RoomEngine
from backend.room_loader import load_room
from backend.seeds import get_or_seed_default_room, seed_default_rooms
from backend.models import Room
from backend.room_state import RoomState


async def test_load_room_maps_terrain_and_enemies(session):
    room = await seed_default_rooms(session)
    template = await load_room(session, room.id)

    assert template.room_id == room.id
    assert template.room_name == "The Pillared Hall"
    assert (template.width, template.height, template.capacity) == (10, 10, 4)
    assert template.spawn_points == [(3, 8), (4, 8), (5, 8), (4, 7)]

    # Terrain -> walls: border + pillars are walls; doors are NOT (passable).
    assert (0, 0) in template.walls          # border
    assert (3, 2) in template.walls          # pillar
    assert (4, 0) not in template.walls      # far door
    assert (4, 9) not in template.walls      # entry door

    # Enemy stats resolved from enemy_defs, not stored in the room.
    assert len(template.enemies) == 4
    goblin = next(e for e in template.enemies if e.name == "Goblin")
    assert (goblin.hp, goblin.attack_damage, goblin.defense) == (6, 1, 1)
    assert goblin.position == (4, 3)

    assert len(template.objects) == 3
    chest = template.objects[0]
    assert chest.to_summary_dict() == {
        "id": "object_1",
        "type": "chest",
        "position": [1, 1],
        "label": "Chest",
        "occupied_cells": [[1, 1]],
        "blocks_movement": True,
        "image": None,
        "visual_size": [1, 1],
        "opened": False,
        "contents_count": 0,
    }
    assert chest.description
    assert "loot" not in chest.to_summary_dict()


async def test_room_state_built_from_template(session):
    room = await seed_default_rooms(session)
    template = await load_room(session, room.id)

    room = RoomState(template, seed=42)

    assert room.walls == template.walls
    assert room.is_valid_position(4, 0) is True    # door is walkable
    assert room.is_valid_position(0, 0) is False   # wall blocks
    assert len(room.enemies) == 4
    # The Goblin's tile holds an enemy occupant on the grid.
    assert room.grid[3][4] is not None
    assert room.grid[3][4].startswith("enemy_")

    state = room.to_dict()
    assert state["room"]["name"] == "The Pillared Hall"
    assert (state["room"]["width"], state["room"]["height"]) == (10, 10)
    assert {exit["label"] for exit in state["exits"]} == {"The Antechamber"}
    assert state["objects"][0] == {
        "id": "object_1",
        "type": "chest",
        "position": [1, 1],
        "label": "Chest",
        "occupied_cells": [[1, 1]],
        "blocks_movement": True,
        "image": None,
        "visual_size": [1, 1],
        "opened": False,
        "contents_count": 0,
    }
    assert room.get_object("object_1").description


async def test_room_capacity_from_spawn_points(session):
    room = await seed_default_rooms(session)
    template = await load_room(session, room.id)
    engine = RoomEngine(template)

    for i in range(template.capacity):          # 4 spawn points -> 4 players
        engine.join(f"p{i}")
    with pytest.raises(ValueError, match="full"):
        engine.join("one_too_many")


async def test_load_room_includes_connections(session):
    hall = await seed_default_rooms(session)
    ante = (await session.execute(
        select(Room).where(Room.name == "The Antechamber"))).scalars().one()

    hall_level = await load_room(session, hall.id)
    assert hall_level.connections == {(4, 0): ante.id, (4, 9): ante.id}
    assert [exit.to_dict() for exit in hall_level.exits] == [
        {"position": [4, 0], "to_room_id": ante.id, "label": "The Antechamber"},
        {"position": [4, 9], "to_room_id": ante.id, "label": "The Antechamber"},
    ]

    ante_level = await load_room(session, ante.id)
    assert ante_level.connections == {(0, 2): hall.id}
    assert ante_level.exits[0].label == "The Pillared Hall"


async def test_load_room_without_connections_has_empty_dict(session):
    room = Room(
        name="Dead End", width=3, height=3,
        terrain=["###", "#.#", "###"],
        spawn_points=[[1, 1]], enemy_spawns=[], objects=[],
    )
    session.add(room)
    await session.commit()

    template = await load_room(session, room.id)
    assert template.connections == {}
    assert template.exits == []


async def test_get_or_seed_is_idempotent(session):
    first = await get_or_seed_default_room(session)
    again = await get_or_seed_default_room(session)

    assert first.id == again.id
    assert first.name == "Oakrun Crossroads"
    # Exactly one starting slice (town + north road), never duplicated.
    count = (await session.execute(select(func.count()).select_from(Room))).scalar_one()
    assert count == 2
