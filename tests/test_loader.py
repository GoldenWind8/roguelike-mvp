"""Tests for loading a DB room into the runtime world (issue #21)."""
import pytest
from sqlalchemy import func, select

from backend.game import Game
from backend.level_loader import load_level
from backend.levels import get_or_seed_default_room, seed_default_level
from backend.models import Room
from backend.world import WorldState


async def test_load_level_maps_terrain_and_enemies(session):
    room = await seed_default_level(session)
    level = await load_level(session, room.id)

    assert (level.width, level.height, level.capacity) == (10, 10, 4)
    assert level.spawn_points == [(3, 8), (4, 8), (5, 8), (4, 7)]

    # Terrain -> walls: border + pillars are walls; doors are NOT (passable).
    assert (0, 0) in level.walls          # border
    assert (3, 2) in level.walls          # pillar
    assert (4, 0) not in level.walls      # far door
    assert (4, 9) not in level.walls      # entry door

    # Enemy stats resolved from enemy_defs, not stored in the room.
    assert len(level.enemies) == 4
    goblin = next(e for e in level.enemies if e.name == "Goblin")
    assert (goblin.hp, goblin.attack_damage, goblin.defense) == (6, 1, 1)
    assert goblin.position == (4, 3)


async def test_worldstate_built_from_level(session):
    room = await seed_default_level(session)
    level = await load_level(session, room.id)

    world = WorldState(level, seed=42)

    assert world.walls == level.walls
    assert world.is_valid_position(4, 0) is True    # door is walkable
    assert world.is_valid_position(0, 0) is False   # wall blocks
    assert len(world.enemies) == 4
    # The Goblin's tile holds an enemy occupant on the grid.
    assert world.grid[3][4] is not None
    assert world.grid[3][4].startswith("enemy_")


async def test_game_capacity_from_spawn_points(session):
    room = await seed_default_level(session)
    level = await load_level(session, room.id)
    game = Game(level)

    for i in range(level.capacity):          # 4 spawn points -> 4 players
        game.join(f"p{i}")
    with pytest.raises(ValueError, match="full"):
        game.join("one_too_many")


async def test_get_or_seed_is_idempotent(session):
    first = await get_or_seed_default_room(session)
    again = await get_or_seed_default_room(session)

    assert first.id == again.id
    # Only one world was seeded (default + antechamber), not two.
    count = (await session.execute(select(func.count()).select_from(Room))).scalar_one()
    assert count == 2
