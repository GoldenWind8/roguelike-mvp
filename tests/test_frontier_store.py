from sqlalchemy import func, select

from backend.models import (
    CarriageStop,
    FrontierExit,
    FrontierNode,
    Room,
    RoomConnection,
)
from backend.procgen.frontier_store import (
    AuthoredGateway,
    available_authored_gateways,
    materialize_frontier_exit,
)
from backend.room_loader import load_room
from backend.seeds import get_or_seed_default_room


async def _fieldsite(session):
    await get_or_seed_default_room(session)
    return (await session.execute(
        select(Room).where(Room.content_id == "oakrun_fieldsite_verge")
    )).scalar_one()


async def test_authored_fieldsite_exposes_one_persistent_frontier_door(session):
    fieldsite = await _fieldsite(session)
    template = await load_room(session, fieldsite.id)
    assert template.frontier_exits == {
        (16, 6): "The road beyond the severed maps"
    }
    row = (await session.execute(
        select(FrontierExit).where(
            FrontierExit.source_room_id == fieldsite.id
        )
    )).scalar_one()
    assert row.status == "frontier"
    assert row.biome_hint == "amberfall_fields"


async def test_materializing_frontier_creates_reproducible_room_and_edges(session):
    fieldsite = await _fieldsite(session)
    before = (await session.execute(
        select(func.count()).select_from(Room)
    )).scalar_one()

    expansion = await materialize_frontier_exit(
        session,
        source_room_id=fieldsite.id,
        source_x=16,
        source_y=6,
    )
    assert expansion.created_room
    assert expansion.depth == 1
    assert expansion.biome == "amberfall_fields"

    after = (await session.execute(
        select(func.count()).select_from(Room)
    )).scalar_one()
    assert after == before + 1

    target = await session.get(Room, expansion.target_room_id)
    node = (await session.execute(
        select(FrontierNode).where(FrontierNode.room_id == target.id)
    )).scalar_one()
    assert node.generator_kind == "frontier_wilds"
    assert node.generation_metadata["source_exit"] == f"{fieldsite.id}:16:6"

    source_edge = (await session.execute(
        select(RoomConnection).where(
            RoomConnection.from_room_id == fieldsite.id,
            RoomConnection.from_x == 16,
            RoomConnection.from_y == 6,
        )
    )).scalar_one()
    assert source_edge.to_room_id == target.id
    reverse = (await session.execute(
        select(RoomConnection).where(
            RoomConnection.from_room_id == target.id,
            RoomConnection.to_room_id == fieldsite.id,
        )
    )).scalars().all()
    assert len(reverse) == 1

    template = await load_room(session, target.id)
    assert template.connections
    assert template.frontier_exits


async def test_retrying_materialized_exit_does_not_duplicate_world(session):
    fieldsite = await _fieldsite(session)
    first = await materialize_frontier_exit(
        session,
        source_room_id=fieldsite.id,
        source_x=16,
        source_y=6,
    )
    count_after_first = (await session.execute(
        select(func.count()).select_from(Room)
    )).scalar_one()
    second = await materialize_frontier_exit(
        session,
        source_room_id=fieldsite.id,
        source_x=16,
        source_y=6,
    )
    count_after_second = (await session.execute(
        select(func.count()).select_from(Room)
    )).scalar_one()
    assert second.target_room_id == first.target_room_id
    assert not second.created_room
    assert count_after_second == count_after_first


async def test_generated_children_inherit_rising_region_pressure(session):
    fieldsite = await _fieldsite(session)
    first = await materialize_frontier_exit(
        session,
        source_room_id=fieldsite.id,
        source_x=16,
        source_y=6,
    )
    children = (await session.execute(
        select(FrontierExit).where(
            FrontierExit.source_room_id == first.target_room_id,
            FrontierExit.status == "frontier",
        )
    )).scalars().all()
    assert children
    assert {child.discovery_pressure for child in children} == {1.0}


async def test_hard_pity_can_reveal_an_authored_gateway(session):
    fieldsite = await _fieldsite(session)
    frontier = (await session.execute(
        select(FrontierExit).where(
            FrontierExit.source_room_id == fieldsite.id,
            FrontierExit.source_x == 16,
            FrontierExit.source_y == 6,
        )
    )).scalar_one()
    frontier.discovery_pressure = 18

    gateway_room = Room(
        content_id="veyr_mourning_gate",
        name="The Mourning Gate",
        width=8,
        height=8,
        terrain=[
            "###+####",
            "#......#",
            "#......#",
            "#......#",
            "#......#",
            "#......#",
            "#......#",
            "########",
        ],
        objects=[],
        spawn_points=[[3, 1], [2, 1]],
        enemy_spawns=[],
    )
    session.add(gateway_room)
    await session.flush()

    expansion = await materialize_frontier_exit(
        session,
        source_room_id=fieldsite.id,
        source_x=16,
        source_y=6,
        authored_gateways=(
            AuthoredGateway(
                region_id="veyr",
                label="Veyr's Mourning Gate",
                room_id=gateway_room.id,
                reverse_x=3,
                reverse_y=0,
                min_depth=1,
            ),
        ),
    )
    assert not expansion.created_room
    assert expansion.discovered_region_id == "veyr"
    assert expansion.target_room_id == gateway_room.id

    await session.refresh(frontier)
    assert frontier.status == "connected"
    assert frontier.target_room_id == gateway_room.id


async def test_seeded_kingdom_gateways_unlock_shared_carriage_travel(session):
    fieldsite = await _fieldsite(session)
    gateways = await available_authored_gateways(session)
    assert {gateway.region_id for gateway in gateways} == {"drazna", "rouvray"}

    drazna = next(gateway for gateway in gateways if gateway.region_id == "drazna")
    stop = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == drazna.room_id)
    )).scalar_one()
    assert stop.status == "closed"

    frontier = (await session.execute(
        select(FrontierExit).where(
            FrontierExit.source_room_id == fieldsite.id,
            FrontierExit.source_x == 16,
            FrontierExit.source_y == 6,
        )
    )).scalar_one()
    frontier.discovery_pressure = 18
    expansion = await materialize_frontier_exit(
        session,
        source_room_id=fieldsite.id,
        source_x=16,
        source_y=6,
        authored_gateways=(
            AuthoredGateway(
                region_id=drazna.region_id,
                label=drazna.label,
                room_id=drazna.room_id,
                reverse_x=drazna.reverse_x,
                reverse_y=drazna.reverse_y,
                min_depth=1,
                weight=drazna.weight,
            ),
        ),
    )
    assert expansion.discovered_region_id == "drazna"
    await session.refresh(stop)
    assert stop.status == "operating"
    assert {gateway.region_id for gateway in await available_authored_gateways(session)} == {
        "rouvray"
    }
