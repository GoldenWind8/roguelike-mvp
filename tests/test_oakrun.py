from sqlalchemy import func, select

from backend.models import NPCRow, PlayerRow, Room
from backend.npc_store import load_npcs
from backend.room_loader import load_room
from backend.seeds import (
    AUTHORED_REGIONS,
    DEFAULT_ROOM,
    NORTH_ROAD_ROOM,
    OAKRUN_ROOM,
    OAKRUN_ROOMS,
    get_or_seed_default_room,
    seed_default_rooms,
)


async def test_oakrun_is_the_seeded_login_destination(session):
    oakrun = await get_or_seed_default_room(session)
    again = await get_or_seed_default_room(session)

    assert oakrun.id == again.id
    assert oakrun.name == OAKRUN_ROOM["name"]
    assert (oakrun.width, oakrun.height) == (25, 19)
    count = (await session.execute(select(func.count()).select_from(Room))).scalar_one()
    assert count == sum(len(region["rooms"]) for region in AUTHORED_REGIONS.values())

    template = await load_room(session, oakrun.id)
    assert template.enemies == []
    assert {obj.type for obj in template.objects} == {
        "wayfarers_rest_exterior",
        "general_goods_shop_exterior",
        "basils_cures_exterior",
        "great_oak",
        "stone_well",
        "covered_carriage",
        "crate_barrel_cluster",
        "noticeboard",
        "hitching_post",
    }
    shop = next(obj for obj in template.objects if obj.id == "oakrun_general_goods_shop")
    assert shop.interaction == "shop"
    noticeboard = next(obj for obj in template.objects if obj.id == "oakrun_noticeboard")
    assert noticeboard.interaction == "noticeboard"
    assert all(obj.image for obj in template.objects)
    assert len(template.connections) == 3
    assert {exit_.label for exit_ in template.exits} == {
        NORTH_ROAD_ROOM["name"],
        "Orchard Lane",
        "Pilgrim's Hollow",
    }
    great_oak = next(obj for obj in template.objects if obj.type == "great_oak")
    assert great_oak.footprint == ((0, 0),)
    assert great_oak.visual_size == (4, 4)


async def test_every_authored_oakrun_room_and_connection_loads(session):
    await get_or_seed_default_room(session)
    rows = (await session.execute(
        select(Room).where(Room.content_id.in_(OAKRUN_ROOMS))
    )).scalars().all()

    templates = [await load_room(session, row.id) for row in rows]

    assert {row.content_id for row in rows} == set(OAKRUN_ROOMS)
    # Eighteen authored Oakrun edges plus the isolated, temporary Drazna
    # playtest bridge from the Fieldsite.
    assert sum(len(template.connections) for template in templates) == 19
    assert all(template.exits for template in templates)


async def test_oakrun_residents_and_north_road_use_accepted_art(session):
    oakrun = await get_or_seed_default_room(session)
    residents = await load_npcs(session, oakrun.id)
    by_name = {npc.name: npc for npc in residents}

    assert set(by_name) == {
        "Basil",
        "Elowen Pike",
        "Tom Weller",
        "Hester Vale",
        "Rowan Hale",
        "Alys Ward",
    }
    assert by_name["Basil"].image == "/art/world/actors/basil-world-v1.png"
    assert by_name["Basil"].to_dict()["visual_size"] == [1, 2]

    road = (await session.execute(
        select(Room).where(Room.name == NORTH_ROAD_ROOM["name"])
    )).scalars().one()
    road_template = await load_room(session, road.id)
    enemies = {enemy.name: enemy for enemy in road_template.enemies}
    assert set(enemies) == {"Rat Pack", "Feral Hound", "Road Bandit"}
    assert enemies["Road Bandit"].image == "/art/world/enemies/road-bandit-v1.png"

    all_rows = (await session.execute(select(NPCRow))).scalars().all()
    oakrun_room_ids = set((await session.execute(
        select(Room.id).where(Room.content_id.in_(OAKRUN_ROOMS))
    )).scalars())
    oakrun_rows = [
        row for row in all_rows
        if row.room_id in oakrun_room_ids
    ]
    assert {row.name for row in oakrun_rows} == {
        "Basil", "Elowen Pike", "Tom Weller", "Hester Vale", "Rowan Hale",
        "Maud Bell", "Alys Ward", "Fen Alder", "Edda Marr", "Wren",
        "Jory Rusk",
    }
    recruitable = {
        row.name for row in oakrun_rows
        if "join_party" in row.persona.get("grants", [])
    }
    assert recruitable == {"Edda Marr", "Wren"}
    # Legacy Oakrun residents retain their hand-authored persona links. Jory
    # belongs to the living-world system, whose relationships are mutable
    # database records rather than frozen persona JSON.
    assert all(
        row.persona.get("relationships")
        for row in oakrun_rows
        if row.name != "Jory Rusk"
    )


async def test_adding_oakrun_migrates_characters_out_of_legacy_demo_rooms(session):
    hall = await seed_default_rooms(session)
    player = PlayerRow(
        id="player_legacy",
        username="legacy",
        password_hash="unused",
        room_id=hall.id,
        x=4,
        y=8,
        hp=50,
    )
    session.add(player)
    await session.commit()

    oakrun = await get_or_seed_default_room(session)
    await session.refresh(player)

    assert hall.name == DEFAULT_ROOM["name"]
    assert player.room_id == oakrun.id
    assert player.x is None and player.y is None


async def test_authored_persona_context_refreshes_without_resetting_npc_life(session):
    await get_or_seed_default_room(session)
    basil = (await session.execute(
        select(NPCRow).where(NPCRow.name == "Basil")
    )).scalars().one()
    stale = dict(basil.persona)
    stale["knowledge"] = ["stale"]
    basil.persona = stale
    basil.hp = 7
    await session.commit()

    await get_or_seed_default_room(session)
    await session.refresh(basil)

    assert basil.hp == 7
    assert basil.persona["knowledge"] != ["stale"]
    assert any("Wren" in relation["name"] for relation in basil.persona["relationships"])
