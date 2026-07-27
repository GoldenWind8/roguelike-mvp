from sqlalchemy import select

from backend.living_world import store
from backend.models import CarriageStop, NPCRow, Room
from backend.room_loader import load_room
from backend.seeds import (
    LIVING_NPC_SEEDS,
    SECONDARY_AUTHORED_NPC_SEEDS,
    get_or_seed_default_room,
)


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
    expected_drazna_art = {
        "mara-vey": "queen_mara_vey",
        "ilya-sorn": "ilya_sorn",
        "nera-bell": "nera_bell",
        "olek-var": "olek_var",
        "pava-mirek": "pava_mirek",
        "vasko-mirek": "vasko_mirek",
        "vesna-korr": "vesna_korr",
        "alin-vey": "alin_vey",
    }
    assert {
        row.content_id: row.persona["art_id"]
        for row in rows
        if row.content_id in expected_drazna_art
    } == expected_drazna_art


async def test_secondary_core_people_seed_in_their_authored_rooms(session):
    await get_or_seed_default_room(session)
    expected = {
        persona["id"]: room_content_id
        for room_content_id, persona, *_ in SECONDARY_AUTHORED_NPC_SEEDS
    }
    rows = (await session.execute(
        select(NPCRow).where(NPCRow.content_id.in_(tuple(expected)))
    )).scalars().all()
    rooms = {
        room.id: room.content_id
        for room in (await session.execute(select(Room))).scalars()
    }
    assert {row.content_id for row in rows} == set(expected)
    assert {
        row.content_id: rooms[row.room_id]
        for row in rows
    } == expected
    odran = next(row for row in rows if row.content_id == "odran-third-bell")
    assert odran.disposition == "hostile"
    assert rooms[odran.room_id] == "drazna_gate_seven"


async def test_drazna_simulation_locations_use_dedicated_room_maps(session):
    await get_or_seed_default_room(session)
    mapping = await store.room_id_by_content(session)
    locations = {
        "drazna_birch_heights",
        "drazna_crown_sluice",
        "drazna_walking_ward",
        "drazna_undertide",
    }
    rows = (await session.execute(
        select(Room).where(Room.content_id.in_(tuple(locations)))
    )).scalars().all()
    expected = {row.content_id: row.id for row in rows}
    assert set(expected) == locations
    assert {location: mapping[location] for location in locations} == expected


async def test_established_drazna_residents_begin_in_dedicated_districts(
    session,
):
    await get_or_seed_default_room(session)
    expected = {
        "mara-vey": "drazna_palace_still_water",
        "alin-vey": "drazna_palace_still_water",
        "ilya-sorn": "drazna_crown_sluice",
        "nera-bell": "drazna_house_of_names",
        "olek-var": "drazna_mud_crown",
        "pava-mirek": "drazna_walking_ward",
        "vasko-mirek": "drazna_undertide",
        "vesna-korr": "drazna_dry_dock",
        "lina-pell": "drazna_lantern_quays",
    }
    rows = (await session.execute(
        select(NPCRow).where(NPCRow.content_id.in_(tuple(expected)))
    )).scalars().all()
    rooms = {
        room.id: room.content_id
        for room in (await session.execute(select(Room))).scalars()
    }
    assert {
        row.content_id: rooms[row.room_id]
        for row in rows
    } == expected
