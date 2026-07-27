import pytest
from sqlalchemy import select

from backend.carriage_store import (
    CarriageError,
    carriage_view,
    discover_stop,
    ensure_carriage_stop,
    name_carriage_stop,
    reachable_destinations,
    resolve_carriage_travel,
)
from backend.models import (
    CarriageRoute,
    CarriageStop,
    PlayerRow,
    Room,
)
from backend.seeds import get_or_seed_default_room


async def _player(session, player_id="player_carriage"):
    row = PlayerRow(
        id=player_id,
        username=player_id,
        password_hash="unused",
        hp=100,
    )
    session.add(row)
    await session.flush()
    return row


async def _room(session, name):
    room = Room(
        name=name,
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
    session.add(room)
    await session.flush()
    return room


async def test_oakrun_seeds_as_the_first_operating_carriage_stop(session):
    oakrun = await get_or_seed_default_room(session)
    stop = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == oakrun.id)
    )).scalar_one()
    assert stop.public_name == "Oakrun Exchange"
    assert stop.status == "operating"


async def test_first_arriving_player_can_name_a_generated_stop(session):
    await get_or_seed_default_room(session)
    player = await _player(session)
    room = await _room(session, "Unnamed Reach")
    stop = await ensure_carriage_stop(
        session,
        stop_key="stop:unnamed-reach",
        room_id=room.id,
        biome="amberfall_fields",
        world_minute=50,
    )
    await discover_stop(
        session,
        player_id=player.id,
        stop_id=stop.id,
        world_minute=60,
    )
    named = await name_carriage_stop(
        session,
        player_id=player.id,
        stop_id=stop.id,
        proposed_name="  Hester's   Turn  ",
        world_minute=65,
    )
    await session.commit()
    assert named.public_name == "Hester's Turn"
    assert named.status == "operating"
    assert named.named_by_player_id == player.id

    routes = (await session.execute(
        select(CarriageRoute).where(
            (CarriageRoute.from_stop_id == stop.id)
            | (CarriageRoute.to_stop_id == stop.id)
        )
    )).scalars().all()
    assert len(routes) == 2  # both directions to Oakrun


async def test_only_an_arriving_player_may_name_a_stop_and_names_are_safe(session):
    await get_or_seed_default_room(session)
    player = await _player(session)
    room = await _room(session, "Blank Mile")
    stop = await ensure_carriage_stop(
        session,
        stop_key="stop:blank-mile",
        room_id=room.id,
        biome="frontier",
        world_minute=0,
    )
    with pytest.raises(CarriageError, match="arrive"):
        await name_carriage_stop(
            session,
            player_id=player.id,
            stop_id=stop.id,
            proposed_name="My Stop",
            world_minute=1,
        )
    await discover_stop(
        session,
        player_id=player.id,
        stop_id=stop.id,
        world_minute=2,
    )
    with pytest.raises(CarriageError, match="letters"):
        await name_carriage_stop(
            session,
            player_id=player.id,
            stop_id=stop.id,
            proposed_name="<script>alert(1)</script>",
            world_minute=3,
        )


async def test_named_stop_becomes_a_public_destination_for_other_players(session):
    oakrun = await get_or_seed_default_room(session)
    first = await _player(session, "player_first")
    other = await _player(session, "player_other")
    room = await _room(session, "Community Verge")
    stop = await ensure_carriage_stop(
        session,
        stop_key="stop:community-verge",
        room_id=room.id,
        biome="veyr_approach",
        world_minute=10,
    )
    await discover_stop(
        session,
        player_id=first.id,
        stop_id=stop.id,
        world_minute=11,
    )
    await name_carriage_stop(
        session,
        player_id=first.id,
        stop_id=stop.id,
        proposed_name="Blackreed Turn",
        world_minute=12,
    )
    await session.commit()

    view = await carriage_view(
        session,
        room_id=oakrun.id,
        player_id=other.id,
        world_minute=20,
    )
    assert [destination["name"] for destination in view["destinations"]] == [
        "Blackreed Turn"
    ]
    destination = await resolve_carriage_travel(
        session,
        from_room_id=oakrun.id,
        destination_stop_id=stop.id,
    )
    assert destination.room_id == room.id
    assert destination.travel_minutes > 0


async def test_unnamed_stop_is_not_a_public_fast_travel_destination(session):
    oakrun = await get_or_seed_default_room(session)
    player = await _player(session)
    room = await _room(session, "Secret Layby")
    await ensure_carriage_stop(
        session,
        stop_key="stop:secret-layby",
        room_id=room.id,
        biome="deep_frontier",
        world_minute=0,
    )
    oakrun_stop = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == oakrun.id)
    )).scalar_one()
    assert await reachable_destinations(session, oakrun_stop.id) == []
    view = await carriage_view(
        session,
        room_id=oakrun.id,
        player_id=player.id,
        world_minute=5,
    )
    assert view["destinations"] == []
