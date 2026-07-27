from sqlalchemy import select

from backend.models import CarriageStop, NPCRow, Room
from backend.room_loader import load_room
from backend.seeds import LIVING_NPC_SEEDS, get_or_seed_default_room


async def test_distant_kingdoms_are_playable_but_frontier_gated(session):
    await get_or_seed_default_room(session)
    drazna = (await session.execute(
        select(Room).where(Room.content_id == "drazna_lantern_quays")
    )).scalar_one()
    rouvary = (await session.execute(
        select(Room).where(Room.content_id == "hollow_bells_post")
    )).scalar_one()

    drazna_template = await load_room(session, drazna.id)
    rouvary_template = await load_room(session, rouvary.id)
    assert drazna_template.room_name == "The Lantern Quays"
    assert rouvary_template.room_name == "Hollow Bells Post"
    assert (0, 6) not in drazna_template.connections
    assert (0, 6) not in rouvary_template.connections
    assert drazna_template.connections
    assert rouvary_template.connections

    stops = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id.in_((drazna.id, rouvary.id)))
    )).scalars().all()
    assert {stop.status for stop in stops} == {"closed"}


async def test_twelve_new_people_exist_as_permanent_individuals(session):
    await get_or_seed_default_room(session)
    expected = {persona["id"] for _, persona, *_ in LIVING_NPC_SEEDS}
    rows = (await session.execute(
        select(NPCRow).where(NPCRow.content_id.in_(expected))
    )).scalars().all()
    assert {row.content_id for row in rows} == expected
    assert len(rows) == 12
    assert all(row.persona["knowledge"] for row in rows)
    assert {
        row.content_id
        for row in rows
        if "join_party" in row.persona.get("grants", [])
    } == {"vasko-mirek", "lina-pell"}
