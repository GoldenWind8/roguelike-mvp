from sqlalchemy import func, select

from backend.models import PlayerRow, Room
from backend.npc_store import load_npcs
from backend.room_loader import load_room
from backend.seeds import (
    DEFAULT_ROOM,
    NORTH_ROAD_ROOM,
    OAKRUN_ROOM,
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
    assert count == 2  # Oakrun plus the first north-road connector.

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
    assert len(template.connections) == 1
    assert template.exits[0].label == NORTH_ROAD_ROOM["name"]
    great_oak = next(obj for obj in template.objects if obj.type == "great_oak")
    assert great_oak.footprint == ((0, 0),)
    assert great_oak.visual_size == (4, 4)


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
        "Maud Bell",
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
