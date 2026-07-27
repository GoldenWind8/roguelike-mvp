from sqlalchemy import select

from backend.carriage_store import (
    DRAZNA_CARRIAGE_ACTIVATION_GROUP,
    carriage_view,
    reachable_destinations,
    resolve_carriage_travel,
)
from backend.config import PLAYER_STARTING_COINS
from backend.models import (
    CarriageRoute,
    CarriageStop,
    FrontierExit,
    PlayerRow,
    Room,
)
from backend.procgen.frontier_store import (
    AuthoredGateway,
    available_authored_gateways,
    materialize_frontier_exit,
)
from backend.room_loader import load_room
from backend.room_state import RoomState
from backend.seeds import get_or_seed_default_room
from backend.shop_store import BUYBACK_PRICES


DRAZNA_STOP_KEYS = {
    "stop:drazna-lantern-quays",
    "stop:drazna-high-crown",
    "stop:drazna-birch-heights",
}

DRAZNA_ROUTE_KEYS = {
    "service:mudwheel:quays-to-crown",
    "service:mudwheel:crown-to-birch",
    "service:mudwheel:birch-to-crown",
    "service:mudwheel:crown-to-quays",
    "service:grey-heron:oakrun-to-drazna",
    "service:grey-heron:drazna-to-oakrun",
}


async def _drazna_stops(session) -> list[CarriageStop]:
    rows = (await session.execute(select(CarriageStop))).scalars().all()
    return [row for row in rows if row.stop_key in DRAZNA_STOP_KEYS]


async def _drazna_routes(session) -> list[CarriageRoute]:
    rows = (await session.execute(select(CarriageRoute))).scalars().all()
    return [
        row
        for row in rows
        if isinstance(row.details, dict)
        and row.details.get("activation_group")
        == DRAZNA_CARRIAGE_ACTIVATION_GROUP
    ]


async def _discover_drazna_from_frontier(session) -> None:
    fieldsite = (await session.execute(
        select(Room).where(Room.content_id == "oakrun_fieldsite_verge")
    )).scalar_one()
    frontier = (await session.execute(
        select(FrontierExit).where(
            FrontierExit.source_room_id == fieldsite.id,
            FrontierExit.source_x == 16,
            FrontierExit.source_y == 6,
        )
    )).scalar_one()
    frontier.discovery_pressure = 18
    drazna = next(
        gateway
        for gateway in await available_authored_gateways(session)
        if gateway.region_id == "drazna"
    )
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


def _hidden_by_object_sprite(room_object, point: tuple[int, int]) -> bool:
    cells = room_object.occupied_cells()
    min_x = min(x for x, _ in cells)
    max_x = max(x for x, _ in cells)
    max_y = max(y for _, y in cells)
    logical_width = max_x - min_x + 1
    visual_width, visual_height = room_object.visual_size
    left = min_x + (logical_width - visual_width) / 2
    right = left + visual_width
    top = max_y + 1 - visual_height
    x, y = point
    return left < x + 0.5 < right and top < y + 0.8 and y <= max_y


async def test_drazna_carriage_seed_is_closed_and_idempotent(session):
    await get_or_seed_default_room(session)

    stops = await _drazna_stops(session)
    routes = await _drazna_routes(session)
    assert {stop.stop_key for stop in stops} == DRAZNA_STOP_KEYS
    assert {stop.status for stop in stops} == {"closed"}
    assert {route.route_key for route in routes} == DRAZNA_ROUTE_KEYS
    assert {route.status for route in routes} == {"closed"}
    first_stop_ids = {stop.stop_key: stop.id for stop in stops}
    first_route_ids = {route.route_key: route.id for route in routes}

    await get_or_seed_default_room(session)

    stops = await _drazna_stops(session)
    routes = await _drazna_routes(session)
    assert {stop.stop_key: stop.id for stop in stops} == first_stop_ids
    assert {route.route_key: route.id for route in routes} == first_route_ids
    assert {stop.status for stop in stops} == {"closed"}
    assert {route.status for route in routes} == {"closed"}


async def test_temporary_bridge_exposes_physical_but_closed_stops(session):
    await get_or_seed_default_room(session)
    player = PlayerRow(
        id="drazna-carriage-inspector",
        username="drazna-carriage-inspector",
        password_hash="unused",
        hp=100,
    )
    session.add(player)
    await session.flush()

    rooms = {
        room.content_id: room
        for room in (await session.execute(
            select(Room).where(Room.content_id.in_((
                "drazna_lantern_quays",
                "drazna_high_crown",
                "drazna_birch_heights",
            )))
        )).scalars()
    }
    stop_by_room = {stop.room_id: stop for stop in await _drazna_stops(session)}
    assert len(rooms) == len(stop_by_room) == 3

    for room in rooms.values():
        stop = stop_by_room[room.id]
        template = await load_room(session, room.id)
        carriage = next(
            obj
            for obj in template.objects
            if obj.id == stop.details["physical_object_id"]
        )
        assert carriage.type == "drazna_mudwheel_stop"
        assert carriage.interaction == "carriage"
        state = RoomState(template, seed=0)
        landing_cluster = state.free_positions_near_object(carriage.id, 4)
        assert len(landing_cluster) == len(set(landing_cluster)) == 4
        exit_tiles = {
            *template.connections.keys(),
            *template.frontier_exits.keys(),
        }
        for point in landing_cluster:
            assert state.is_valid_position(*point)
            assert state.is_occupied(*point) is None
            assert point not in exit_tiles
            assert carriage.distance_from(*point) <= 2
            assert not _hidden_by_object_sprite(carriage, point)
        view = await carriage_view(
            session,
            room_id=room.id,
            player_id=player.id,
            world_minute=480,
        )
        assert view["stop"]["status"] == "closed"
        assert view["service"]["status"] == "unavailable"
        assert view["destinations"] == []


async def test_frontier_discovery_atomically_activates_authored_network(session):
    await get_or_seed_default_room(session)
    await _discover_drazna_from_frontier(session)

    stops = await _drazna_stops(session)
    routes = await _drazna_routes(session)
    assert {stop.status for stop in stops} == {"operating"}
    assert {route.route_key for route in routes} == DRAZNA_ROUTE_KEYS
    assert {
        route.route_key: route.status for route in routes
    } == {
        "service:mudwheel:quays-to-crown": "operating",
        "service:mudwheel:crown-to-birch": "operating",
        "service:mudwheel:birch-to-crown": "operating",
        "service:mudwheel:crown-to-quays": "operating",
        "service:grey-heron:oakrun-to-drazna": "dangerous",
        "service:grey-heron:drazna-to-oakrun": "dangerous",
    }
    # No nearest-stop routes are manufactured while the authored group opens.
    assert all(route.details.get("community_route") is not True for route in routes)
    drazna_stop_ids = {stop.id for stop in stops}
    all_routes = (await session.execute(select(CarriageRoute))).scalars().all()
    touching_drazna = [
        route
        for route in all_routes
        if route.from_stop_id in drazna_stop_ids
        or route.to_stop_id in drazna_stop_ids
    ]
    assert {route.route_key for route in touching_drazna} == DRAZNA_ROUTE_KEYS

    # An ordinary startup synchronization preserves the discovery and remains
    # idempotent rather than closing or duplicating the service.
    await get_or_seed_default_room(session)
    assert {stop.status for stop in await _drazna_stops(session)} == {"operating"}
    assert len(await _drazna_routes(session)) == 6


async def test_mudwheel_schedule_fares_and_return_are_reachable(session):
    await get_or_seed_default_room(session)
    await _discover_drazna_from_frontier(session)
    stops = {stop.stop_key: stop for stop in await _drazna_stops(session)}
    quays = stops["stop:drazna-lantern-quays"]
    crown = stops["stop:drazna-high-crown"]
    birch = stops["stop:drazna-birch-heights"]

    # Monday is day zero; the source document runs uphill Tuesday/Friday.
    monday_0800 = 480
    monday_destinations = await reachable_destinations(
        session,
        quays.id,
        world_minute=monday_0800,
    )
    monday_crown = next(
        item for item in monday_destinations if item.stop_id == crown.id
    )
    assert monday_crown.next_departure_minute == 1440 + 480
    assert monday_crown.wait_minutes == 1440
    assert monday_crown.available_now is False

    tuesday_0800 = 1440 + 480
    destinations = await reachable_destinations(
        session,
        quays.id,
        world_minute=tuesday_0800,
    )
    crown_trip = next(item for item in destinations if item.stop_id == crown.id)
    birch_trip = next(item for item in destinations if item.stop_id == birch.id)
    assert crown_trip.travel_minutes == crown_trip.journey_minutes == 45
    assert crown_trip.fare == 2
    assert crown_trip.danger == 1
    assert crown_trip.available_now is True
    assert birch_trip.travel_minutes == 80
    assert birch_trip.transfer_wait_minutes == 45
    assert birch_trip.journey_minutes == 125
    assert birch_trip.fare == 3
    assert birch_trip.danger == 1

    resolved = await resolve_carriage_travel(
        session,
        from_room_id=quays.room_id,
        destination_stop_id=birch.id,
        world_minute=tuesday_0800,
    )
    assert resolved.route_stop_ids == (quays.id, crown.id, birch.id)
    assert resolved.arrival_minute == tuesday_0800 + 125
    assert resolved.arrival_object_id == "drazna_heights_mudwheel"

    tuesday_1050 = 1440 + 650
    [crown_down, quays_down] = [
        item
        for item in await reachable_destinations(
            session,
            birch.id,
            world_minute=tuesday_1050,
        )
        if item.stop_id in {crown.id, quays.id}
    ]
    by_stop = {
        item.stop_id: item
        for item in (crown_down, quays_down)
    }
    assert by_stop[crown.id].journey_minutes == 35
    assert by_stop[quays.id].journey_minutes == 125
    assert by_stop[quays.id].travel_minutes == 80
    assert by_stop[quays.id].fare == 3


async def test_grey_heron_is_the_only_seeded_external_drazna_service(session):
    oakrun = await get_or_seed_default_room(session)
    await _discover_drazna_from_frontier(session)
    quays = next(
        stop
        for stop in await _drazna_stops(session)
        if stop.stop_key == "stop:drazna-lantern-quays"
    )
    oakrun_stop = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == oakrun.id)
    )).scalar_one()

    wednesday_0530 = 2 * 1440 + 330
    outbound = await resolve_carriage_travel(
        session,
        from_room_id=oakrun.id,
        destination_stop_id=quays.id,
        world_minute=wednesday_0530,
    )
    assert outbound.route_stop_ids == (oakrun_stop.id, quays.id)
    assert outbound.arrival_object_id == "drazna_quay_carriage"
    assert outbound.travel_minutes == outbound.journey_minutes == 1800
    assert outbound.fare == 24
    assert outbound.danger == 5
    assert outbound.route_status == "dangerous"
    # The route is usable from the actual new-account purse. Teo's Reed Market
    # buyback then makes the return earnable from exploration rather than
    # silently stranding a first-time traveller.
    assert PLAYER_STARTING_COINS - outbound.fare == 6

    sunday_0500 = 6 * 1440 + 300
    returning = await resolve_carriage_travel(
        session,
        from_room_id=quays.room_id,
        destination_stop_id=oakrun_stop.id,
        world_minute=sunday_0500,
    )
    assert returning.route_stop_ids == (quays.id, oakrun_stop.id)
    assert returning.arrival_object_id == "oakrun_covered_carriage"
    assert returning.travel_minutes == returning.journey_minutes == 1800
    assert returning.fare == 24
    assert returning.danger == 5
    assert (
        PLAYER_STARTING_COINS
        - outbound.fare
        + 5 * BUYBACK_PRICES["common"]
    ) >= returning.fare
