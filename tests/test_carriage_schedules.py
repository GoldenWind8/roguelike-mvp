import pytest

from backend.carriage_store import (
    CarriageError,
    carriage_view,
    reachable_destinations,
    resolve_carriage_travel,
)
from backend.models import CarriageRoute, CarriageStop, PlayerRow, Room


async def _room(session, name: str) -> Room:
    room = Room(
        name=name,
        width=5,
        height=5,
        terrain=[
            "##+##",
            "#...#",
            "#...#",
            "#...#",
            "#####",
        ],
        objects=[],
        spawn_points=[[2, 1]],
        enemy_spawns=[],
    )
    session.add(room)
    await session.flush()
    return room


async def _stop(session, key: str, name: str) -> CarriageStop:
    room = await _room(session, name)
    stop = CarriageStop(
        stop_key=key,
        room_id=room.id,
        public_name=name,
        biome="test",
        status="operating",
        created_at_minute=0,
        details={},
    )
    session.add(stop)
    await session.flush()
    return stop


async def _route(
    session,
    source: CarriageStop,
    target: CarriageStop,
    *,
    key: str,
    travel_minutes: int,
    departures: list[int],
    fare: int = 1,
    danger: int = 0,
    status: str = "operating",
    details: dict | None = None,
) -> CarriageRoute:
    route = CarriageRoute(
        route_key=key,
        from_stop_id=source.id,
        to_stop_id=target.id,
        travel_minutes=travel_minutes,
        fare=fare,
        danger=danger,
        status=status,
        departures=departures,
        details=dict(details or {}),
    )
    session.add(route)
    await session.flush()
    return route


async def test_destination_view_uses_daily_departures_and_reports_risk(session):
    source = await _stop(session, "source", "Source")
    target = await _stop(session, "target", "Target")
    await _route(
        session,
        source,
        target,
        key="source-target",
        travel_minutes=30,
        departures=[360, 720, 1080],
        fare=4,
        danger=7,
        status="dangerous",
    )

    [destination] = await reachable_destinations(
        session,
        source.id,
        world_minute=400,
    )

    assert destination.next_departure_minute == 720
    assert destination.wait_minutes == 320
    assert destination.travel_minutes == 30
    assert destination.journey_minutes == 350
    assert destination.arrival_minute == 750
    assert destination.route_status == "dangerous"
    assert destination.danger == 7
    assert destination.available_now is False
    view = destination.to_dict()
    assert view["next_departure_minute_of_day"] == 720
    assert view["max_leg_danger"] == 7


async def test_daily_schedule_rolls_over_without_using_a_real_clock(session):
    source = await _stop(session, "roll-source", "Roll Source")
    target = await _stop(session, "roll-target", "Roll Target")
    await _route(
        session,
        source,
        target,
        key="rollover",
        travel_minutes=20,
        departures=[120],
    )

    [destination] = await reachable_destinations(
        session,
        source.id,
        world_minute=1100,
    )

    assert destination.next_departure_minute == 1560
    assert destination.wait_minutes == 460
    assert destination.arrival_minute == 1580


async def test_resolve_enforces_schedule_with_a_small_boarding_grace(session):
    source = await _stop(session, "grace-source", "Grace Source")
    target = await _stop(session, "grace-target", "Grace Target")
    await _route(
        session,
        source,
        target,
        key="grace",
        travel_minutes=30,
        departures=[720],
    )

    with pytest.raises(CarriageError, match="in 20 minutes.*12:00"):
        await resolve_carriage_travel(
            session,
            from_room_id=source.room_id,
            destination_stop_id=target.id,
            world_minute=700,
        )

    destination = await resolve_carriage_travel(
        session,
        from_room_id=source.room_id,
        destination_stop_id=target.id,
        world_minute=713,
    )
    assert destination.available_now is True
    assert destination.wait_minutes == 7
    assert destination.boarding_minute == 720
    assert destination.journey_minutes == 37

    held = await resolve_carriage_travel(
        session,
        from_room_id=source.room_id,
        destination_stop_id=target.id,
        world_minute=727,
    )
    assert held.next_departure_minute == 720
    assert held.wait_minutes == 0
    assert held.boarding_minute == 727


async def test_route_specific_grace_and_delay_are_deterministic(session):
    source = await _stop(session, "delay-source", "Delay Source")
    target = await _stop(session, "delay-target", "Delay Target")
    await _route(
        session,
        source,
        target,
        key="delayed",
        travel_minutes=10,
        departures=[300],
        status="delayed",
        details={"delay_minutes": 20, "boarding_grace_minutes": 3},
    )

    [destination] = await reachable_destinations(
        session,
        source.id,
        world_minute=317,
    )
    assert destination.next_departure_minute == 320
    assert destination.boarding_grace_minutes == 3
    assert destination.route_status == "delayed"
    assert destination.available_now is True

    with pytest.raises(CarriageError, match="not boarding"):
        await resolve_carriage_travel(
            session,
            from_room_id=source.room_id,
            destination_stop_id=target.id,
            world_minute=316,
            boarding_grace_minutes=2,
        )


async def test_multi_leg_path_waits_for_the_next_feasible_connection(session):
    source = await _stop(session, "multi-source", "Multi Source")
    interchange = await _stop(session, "multi-middle", "Multi Middle")
    target = await _stop(session, "multi-target", "Multi Target")
    await _route(
        session,
        source,
        interchange,
        key="multi-first",
        travel_minutes=30,
        departures=[100],
        fare=2,
        danger=1,
    )
    await _route(
        session,
        interchange,
        target,
        key="multi-second",
        travel_minutes=10,
        departures=[110, 200],
        fare=3,
        danger=4,
    )

    destinations = await reachable_destinations(
        session,
        source.id,
        world_minute=95,
    )
    destination = next(item for item in destinations if item.stop_id == target.id)

    assert destination.route_stop_ids == (
        source.id,
        interchange.id,
        target.id,
    )
    assert destination.wait_minutes == 5
    assert destination.transfer_wait_minutes == 70
    assert destination.travel_minutes == 40
    assert destination.journey_minutes == 115
    assert destination.leg_departure_minutes == (100, 200)
    assert destination.leg_arrival_minutes == (130, 210)
    assert destination.fare == 5
    assert destination.danger == 5


async def test_resolution_can_choose_a_slower_itinerary_boarding_now(session):
    source = await _stop(session, "choice-source", "Choice Source")
    interchange = await _stop(session, "choice-middle", "Choice Middle")
    target = await _stop(session, "choice-target", "Choice Target")
    await _route(
        session,
        source,
        interchange,
        key="choice-later",
        travel_minutes=1,
        departures=[100],
    )
    await _route(
        session,
        interchange,
        target,
        key="choice-connection",
        travel_minutes=1,
        departures=[],
    )
    direct = await _route(
        session,
        source,
        target,
        key="choice-now",
        travel_minutes=50,
        departures=[80],
    )

    future = await reachable_destinations(
        session,
        source.id,
        world_minute=80,
        boarding_grace_minutes=0,
    )
    fastest = next(item for item in future if item.stop_id == target.id)
    assert fastest.route_ids != (direct.id,)
    assert fastest.arrival_minute == 102

    resolved = await resolve_carriage_travel(
        session,
        from_room_id=source.room_id,
        destination_stop_id=target.id,
        world_minute=80,
        boarding_grace_minutes=0,
    )
    assert resolved.route_ids == (direct.id,)
    assert resolved.arrival_minute == 130


async def test_empty_departures_are_explicitly_on_demand_and_legacy_safe(session):
    source = await _stop(session, "legacy-source", "Legacy Source")
    target = await _stop(session, "legacy-target", "Legacy Target")
    await _route(
        session,
        source,
        target,
        key="legacy",
        travel_minutes=25,
        departures=[],
    )

    [scheduled_view] = await reachable_destinations(
        session,
        source.id,
        world_minute=543,
    )
    assert scheduled_view.available_now is True
    assert scheduled_view.wait_minutes == 0
    assert scheduled_view.next_departure_minute is None
    assert scheduled_view.to_dict()["next_departure_minute_of_day"] is None
    assert scheduled_view.boarding_minute == 543
    assert scheduled_view.arrival_minute == 568

    [legacy_view] = await reachable_destinations(session, source.id)
    assert legacy_view.available_now is True
    assert legacy_view.wait_minutes == 0
    assert legacy_view.journey_minutes == legacy_view.travel_minutes == 25
    assert legacy_view.arrival_minute is None

    resolved = await resolve_carriage_travel(
        session,
        from_room_id=source.room_id,
        destination_stop_id=target.id,
    )
    assert resolved.room_id == target.room_id


async def test_boolean_destination_id_cannot_alias_numeric_stop_one(session):
    source = await _stop(session, "bool-source", "Bool Source")
    target = await _stop(session, "bool-target", "Bool Target")
    await _route(
        session,
        source,
        target,
        key="bool-route",
        travel_minutes=10,
        departures=[],
    )

    with pytest.raises(CarriageError, match="real carriage stop"):
        await resolve_carriage_travel(
            session,
            from_room_id=source.room_id,
            destination_stop_id=True,
        )


async def test_carriage_view_summarizes_the_next_service(session):
    player = PlayerRow(
        id="schedule-player",
        username="schedule-player",
        password_hash="unused",
        hp=100,
    )
    session.add(player)
    source = await _stop(session, "view-source", "View Source")
    target = await _stop(session, "view-target", "View Target")
    await _route(
        session,
        source,
        target,
        key="view-route",
        travel_minutes=15,
        departures=[600],
    )
    await session.flush()

    view = await carriage_view(
        session,
        room_id=source.room_id,
        player_id=player.id,
        world_minute=580,
    )

    assert view["service"] == {
        "world_minute": 580,
        "minute_of_day": 580,
        "status": "waiting",
        "next_departure_minute": 600,
        "wait_minutes": 20,
    }
    assert view["destinations"][0]["route_status"] == "operating"


async def test_closed_route_or_intermediate_stop_never_leaks_a_destination(
    session,
):
    source = await _stop(session, "closed-source", "Closed Source")
    middle = await _stop(session, "closed-middle", "Closed Middle")
    target = await _stop(session, "closed-target", "Closed Target")
    await _route(
        session,
        source,
        middle,
        key="open-first-leg",
        travel_minutes=10,
        departures=[],
    )
    second = await _route(
        session,
        middle,
        target,
        key="closed-second-leg",
        travel_minutes=10,
        departures=[],
        status="closed",
    )

    visible = await reachable_destinations(
        session,
        source.id,
        world_minute=100,
    )
    assert {destination.stop_id for destination in visible} == {middle.id}

    second.status = "operating"
    middle.status = "closed"
    await session.flush()
    assert await reachable_destinations(
        session,
        source.id,
        world_minute=100,
    ) == []


async def test_mixed_multi_leg_status_is_explicit_in_public_contract(session):
    source = await _stop(session, "mixed-source", "Mixed Source")
    middle = await _stop(session, "mixed-middle", "Mixed Middle")
    target = await _stop(session, "mixed-target", "Mixed Target")
    await _route(
        session,
        source,
        middle,
        key="mixed-delayed",
        travel_minutes=10,
        departures=[],
        status="delayed",
    )
    await _route(
        session,
        middle,
        target,
        key="mixed-dangerous",
        travel_minutes=10,
        departures=[],
        status="dangerous",
    )

    destination = next(
        item
        for item in await reachable_destinations(
            session,
            source.id,
            world_minute=100,
        )
        if item.stop_id == target.id
    )
    assert destination.route_status == "mixed"
    assert destination.route_statuses == ("delayed", "dangerous")


async def test_corrupt_explicit_weekday_schedule_fails_closed(session):
    source = await _stop(session, "bad-day-source", "Bad Day Source")
    target = await _stop(session, "bad-day-target", "Bad Day Target")
    await _route(
        session,
        source,
        target,
        key="bad-weekday",
        travel_minutes=10,
        departures=[100],
        details={"service_days": ["funday"]},
    )

    assert await reachable_destinations(
        session,
        source.id,
        world_minute=100,
    ) == []

    source_two = await _stop(session, "bad-time-source", "Bad Time Source")
    target_two = await _stop(session, "bad-time-target", "Bad Time Target")
    await _route(
        session,
        source_two,
        target_two,
        key="bad-departure",
        travel_minutes=10,
        departures=[True],
        details={"service_days": ["monday"]},
    )
    assert await reachable_destinations(
        session,
        source_two.id,
        world_minute=100,
    ) == []
