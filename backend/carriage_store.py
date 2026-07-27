"""Shared, community-named carriage-stop network."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import heapq
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    CarriageRoute,
    CarriageStop,
    FrontierNode,
    PlayerCarriageStop,
)

CARRIAGE_STOP_NAME_LIMIT = 32
MINUTES_PER_DAY = 24 * 60
DRAZNA_CARRIAGE_ACTIVATION_GROUP = "authored:drazna"
# A traveller standing beside the coach should not miss it because a routine
# action advanced the shared clock by a handful of minutes. Routes may
# override this in ``details.boarding_grace_minutes``.
DEFAULT_BOARDING_GRACE_MINUTES = 10
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\-]{1,31}$")


class CarriageError(ValueError):
    pass


@dataclass(frozen=True)
class CarriageDestination:
    stop_id: int
    name: str
    room_id: int
    travel_minutes: int
    fare: int
    route_stop_ids: tuple[int, ...]
    arrival_object_id: str | None = None
    wait_minutes: int = 0
    transfer_wait_minutes: int = 0
    journey_minutes: int = 0
    next_departure_minute: int | None = None
    boarding_minute: int | None = None
    arrival_minute: int | None = None
    available_now: bool = True
    boarding_grace_minutes: int = 0
    route_status: str = "operating"
    route_statuses: tuple[str, ...] = ()
    danger: int = 0
    max_leg_danger: int = 0
    route_ids: tuple[int, ...] = ()
    leg_departure_minutes: tuple[int, ...] = ()
    leg_arrival_minutes: tuple[int, ...] = ()

    def to_dict(self) -> dict:
        return {
            "stop_id": self.stop_id,
            "name": self.name,
            "room_id": self.room_id,
            "travel_minutes": self.travel_minutes,
            "fare": self.fare,
            "route_stop_ids": list(self.route_stop_ids),
            "wait_minutes": self.wait_minutes,
            "transfer_wait_minutes": self.transfer_wait_minutes,
            "journey_minutes": self.journey_minutes,
            "next_departure_minute": self.next_departure_minute,
            "next_departure_minute_of_day": (
                self.next_departure_minute % MINUTES_PER_DAY
                if self.next_departure_minute is not None
                else None
            ),
            "boarding_minute": self.boarding_minute,
            "arrival_minute": self.arrival_minute,
            "available_now": self.available_now,
            "boarding_grace_minutes": self.boarding_grace_minutes,
            "route_status": self.route_status,
            "route_statuses": list(self.route_statuses),
            "danger": self.danger,
            "max_leg_danger": self.max_leg_danger,
            "route_ids": list(self.route_ids),
            "leg_departure_minutes": list(self.leg_departure_minutes),
            "leg_arrival_minutes": list(self.leg_arrival_minutes),
        }


async def ensure_carriage_stop(
    session: AsyncSession,
    *,
    stop_key: str,
    room_id: int,
    biome: str,
    world_minute: int,
    public_name: str | None = None,
    metadata: dict | None = None,
    status: str | None = None,
) -> CarriageStop:
    row = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == room_id)
    )).scalars().first()
    if row is not None:
        return row
    initial_status = status or ("operating" if public_name else "unnamed")
    row = CarriageStop(
        stop_key=stop_key,
        room_id=room_id,
        public_name=public_name,
        biome=biome,
        status=initial_status,
        created_at_minute=world_minute,
        details=dict(metadata or {}),
    )
    session.add(row)
    await session.flush()
    if row.status == "operating":
        await _connect_new_stop(session, row)
    return row


async def activate_carriage_stop(
    session: AsyncSession,
    *,
    room_id: int,
) -> CarriageStop | None:
    """Open an authored stop when its kingdom is first reached.

    Once opened it becomes a shared discovery: every player can see the new
    destination from the operating network, just like a community-named
    generated waystop.
    """
    stop = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == room_id)
    )).scalars().first()
    if stop is None:
        return None
    details = stop.details if isinstance(stop.details, dict) else {}
    activation_group = details.get("activation_group")
    if (
        isinstance(activation_group, str)
        and activation_group
        and stop.public_name
    ):
        # Authored service groups open as one transaction. This is deliberately
        # separate from the community waystop connector: revealing Drazna
        # should open the Mudwheel and its one authored outside service, not
        # manufacture arbitrary links to whichever three stops happen to have
        # the lowest ids.
        stops = (await session.execute(select(CarriageStop))).scalars().all()
        grouped_stops = [
            candidate
            for candidate in stops
            if isinstance(candidate.details, dict)
            and candidate.details.get("activation_group") == activation_group
        ]
        grouped_stop_ids = {candidate.id for candidate in grouped_stops}
        for candidate in grouped_stops:
            if candidate.public_name and candidate.status == "closed":
                candidate.status = "operating"

        routes = (await session.execute(select(CarriageRoute))).scalars().all()
        for route in routes:
            route_details = (
                route.details if isinstance(route.details, dict) else {}
            )
            if route_details.get("activation_group") != activation_group:
                continue
            if (
                route.from_stop_id not in grouped_stop_ids
                and route.to_stop_id not in grouped_stop_ids
            ):
                continue
            activation_status = route_details.get(
                "activation_status",
                "operating",
            )
            if activation_status not in ("operating", "delayed", "dangerous"):
                activation_status = "operating"
            route.status = activation_status
        return stop

    if stop.status == "closed" and stop.public_name:
        stop.status = "operating"
        await _connect_new_stop(session, stop)
    return stop


async def discover_stop(
    session: AsyncSession,
    *,
    player_id: str,
    stop_id: int,
    world_minute: int,
) -> None:
    known = (await session.execute(
        select(PlayerCarriageStop).where(
            PlayerCarriageStop.player_id == player_id,
            PlayerCarriageStop.stop_id == stop_id,
        )
    )).scalars().first()
    if known is None:
        session.add(PlayerCarriageStop(
            player_id=player_id,
            stop_id=stop_id,
            first_arrived_minute=world_minute,
            last_arrived_minute=world_minute,
        ))
    else:
        known.last_arrived_minute = world_minute


async def name_carriage_stop(
    session: AsyncSession,
    *,
    player_id: str,
    stop_id: int,
    proposed_name: object,
    world_minute: int,
) -> CarriageStop:
    name = _normalize_name(proposed_name)
    stop = await session.get(CarriageStop, stop_id)
    if stop is None:
        raise CarriageError("That waystop no longer exists.")
    if stop.public_name is not None or stop.status != "unnamed":
        raise CarriageError("Another traveller has already named this stop.")
    known = (await session.execute(
        select(PlayerCarriageStop).where(
            PlayerCarriageStop.player_id == player_id,
            PlayerCarriageStop.stop_id == stop_id,
        )
    )).scalars().first()
    if known is None:
        raise CarriageError("You must arrive at a waystop before naming it.")
    duplicate = (await session.execute(
        select(CarriageStop).where(CarriageStop.public_name == name)
    )).scalars().first()
    if duplicate is not None:
        raise CarriageError("That name is already painted on another stop.")

    stop.public_name = name
    stop.named_by_player_id = player_id
    stop.named_at_minute = world_minute
    stop.status = "operating"
    await _connect_new_stop(session, stop)
    return stop


async def carriage_view(
    session: AsyncSession,
    *,
    room_id: int,
    player_id: str,
    world_minute: int,
    boarding_grace_minutes: int | None = None,
) -> dict:
    stop = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == room_id)
    )).scalars().first()
    if stop is None:
        raise CarriageError("No carriage service stops here.")
    await discover_stop(
        session,
        player_id=player_id,
        stop_id=stop.id,
        world_minute=world_minute,
    )
    destinations = await reachable_destinations(
        session,
        stop.id,
        world_minute=world_minute,
        boarding_grace_minutes=boarding_grace_minutes,
    )
    await session.commit()
    next_destination = min(
        destinations,
        key=lambda item: (
            item.wait_minutes,
            item.next_departure_minute
            if item.next_departure_minute is not None
            else world_minute,
            item.name,
        ),
        default=None,
    )
    return {
        "stop": _stop_view(stop),
        "destinations": [destination.to_dict() for destination in destinations],
        "can_name": stop.status == "unnamed" and stop.public_name is None,
        "name_limit": CARRIAGE_STOP_NAME_LIMIT,
        "service": {
            "world_minute": world_minute,
            "minute_of_day": world_minute % MINUTES_PER_DAY,
            "status": (
                "boarding"
                if any(item.available_now for item in destinations)
                else "waiting"
                if destinations
                else "unavailable"
            ),
            "next_departure_minute": (
                next_destination.next_departure_minute
                if next_destination is not None
                else None
            ),
            "wait_minutes": (
                next_destination.wait_minutes
                if next_destination is not None
                else None
            ),
        },
    }


async def reachable_destinations(
    session: AsyncSession,
    from_stop_id: int,
    *,
    world_minute: int | None = None,
    boarding_grace_minutes: int | None = None,
    require_boardable_now: bool = False,
) -> list[CarriageDestination]:
    """Return earliest feasible itineraries through the operating network.

    Persisted departure values are minutes-of-day and recur every in-world
    day.  Passing no ``world_minute`` retains the old immediate-service
    behaviour for older callers.  Routes with no valid departure values are
    explicitly treated as on-demand, which also preserves pre-schedule and
    community-generated data.
    """
    _validate_schedule_options(
        world_minute=world_minute,
        boarding_grace_minutes=boarding_grace_minutes,
    )
    stops = (await session.execute(
        select(CarriageStop).where(CarriageStop.status == "operating")
    )).scalars().all()
    stop_by_id = {stop.id: stop for stop in stops}
    if from_stop_id not in stop_by_id:
        return []
    routes = (await session.execute(
        select(CarriageRoute).where(
            CarriageRoute.status.in_(("operating", "delayed", "dangerous"))
        )
    )).scalars().all()
    route_by_id = {route.id: route for route in routes}
    adjacency: dict[int, list[CarriageRoute]] = {}
    for route in routes:
        if (
            route.from_stop_id not in stop_by_id
            or route.to_stop_id not in stop_by_id
        ):
            continue
        # A route that explicitly opts into weekday service but has corrupt
        # or empty day metadata is unavailable, never silently daily.
        service_days = _route_service_days(route)
        if service_days == ():
            continue
        if service_days is not None and not _route_departures(route):
            continue
        adjacency.setdefault(route.from_stop_id, []).append(route)
    for outgoing in adjacency.values():
        outgoing.sort(key=lambda route: (route.to_stop_id, route.id))

    # The shared network is small. A time-dependent Dijkstra is sufficient
    # because arriving earlier at a stop can always wait for every service an
    # otherwise-identical later arrival could catch.
    start_minute = world_minute if world_minute is not None else 0
    queue: list[tuple[
        int,
        int,
        int,
        tuple[int, ...],
        int,
        int,
        tuple[int, ...],
        tuple[str, ...],
        tuple[int, ...],
        tuple[int, ...],
        tuple[int | None, ...],
        tuple[int, ...],
        tuple[int, ...],
    ]] = [
        (
            start_minute,
            0,
            0,
            (from_stop_id,),
            from_stop_id,
            0,
            (),
            (),
            (),
            (),
            (),
            (),
            (),
        )
    ]
    best: dict[int, tuple[int, int, int, tuple[int, ...]]] = {}
    itineraries: dict[int, tuple[
        int,
        int,
        int,
        tuple[int, ...],
        int,
        tuple[int, ...],
        tuple[str, ...],
        tuple[int, ...],
        tuple[int, ...],
        tuple[int | None, ...],
        tuple[int, ...],
        tuple[int, ...],
    ]] = {}
    while queue:
        (
            arrival,
            fare,
            danger,
            path,
            stop_id,
            ride_minutes,
            waits,
            statuses,
            route_ids,
            route_dangers,
            scheduled_departures,
            actual_departures,
            leg_arrivals,
        ) = heapq.heappop(queue)
        score = (arrival, fare, danger, path)
        if stop_id in best and best[stop_id] <= score:
            continue
        best[stop_id] = score
        itineraries[stop_id] = (
            arrival,
            fare,
            danger,
            path,
            ride_minutes,
            waits,
            statuses,
            route_ids,
            route_dangers,
            scheduled_departures,
            actual_departures,
            leg_arrivals,
        )
        for route in adjacency.get(stop_id, ()):
            grace = _route_boarding_grace(
                route,
                override=boarding_grace_minutes,
            )
            if world_minute is None:
                scheduled_departure = None
                actual_departure = arrival
                wait = 0
            else:
                (
                    scheduled_departure,
                    actual_departure,
                    wait,
                ) = _next_route_departure(
                    route,
                    ready_minute=arrival,
                    boarding_grace_minutes=grace,
                )
            if (
                require_boardable_now
                and not route_ids
                and not _is_boardable_now(
                    world_minute=world_minute,
                    scheduled_departure=scheduled_departure,
                    boarding_grace_minutes=grace,
                )
            ):
                continue
            next_arrival = actual_departure + route.travel_minutes
            heapq.heappush(
                queue,
                (
                    next_arrival,
                    fare + route.fare,
                    danger + route.danger,
                    (*path, route.to_stop_id),
                    route.to_stop_id,
                    ride_minutes + route.travel_minutes,
                    (*waits, wait),
                    (*statuses, route.status),
                    (*route_ids, route.id),
                    (*route_dangers, route.danger),
                    (*scheduled_departures, scheduled_departure),
                    (*actual_departures, actual_departure),
                    (*leg_arrivals, next_arrival),
                ),
            )

    destinations = []
    for stop_id, itinerary in itineraries.items():
        if stop_id == from_stop_id:
            continue
        stop = stop_by_id.get(stop_id)
        if stop is None or not stop.public_name:
            continue
        (
            arrival,
            fare,
            danger,
            path,
            ride_minutes,
            waits,
            statuses,
            route_ids,
            route_dangers,
            scheduled_departures,
            actual_departures,
            leg_arrivals,
        ) = itinerary
        embedded_layover_minutes = sum(
            _route_embedded_layover(route_by_id[route_id])
            for route_id in route_ids
            if route_id in route_by_id
        )
        first_scheduled = scheduled_departures[0]
        first_grace = _route_boarding_grace(
            next(
                route
                for route in adjacency[from_stop_id]
                if route.id == route_ids[0]
            ),
            override=boarding_grace_minutes,
        )
        initial_wait = waits[0] if waits else 0
        destinations.append(CarriageDestination(
            stop_id=stop.id,
            name=stop.public_name,
            room_id=stop.room_id,
            # A terminal alias may collapse an authored intermediate stop into
            # one physical route row. Its persisted duration still advances
            # the world through that layover, while the client receives the
            # moving and stationary portions separately.
            travel_minutes=ride_minutes - embedded_layover_minutes,
            fare=fare,
            route_stop_ids=path,
            arrival_object_id=(
                stop.details.get("physical_object_id")
                if (
                    isinstance(stop.details, dict)
                    and isinstance(
                        stop.details.get("physical_object_id"),
                        str,
                    )
                    and stop.details["physical_object_id"]
                )
                else None
            ),
            wait_minutes=initial_wait,
            transfer_wait_minutes=(
                sum(waits[1:]) + embedded_layover_minutes
            ),
            journey_minutes=arrival - start_minute,
            # ``None`` is the public contract for an on-demand first leg.
            # ``boarding_minute`` still records the immediate effective
            # departure, while the UI can distinguish it from a timetable.
            next_departure_minute=first_scheduled,
            boarding_minute=actual_departures[0],
            arrival_minute=arrival if world_minute is not None else None,
            available_now=_is_boardable_now(
                world_minute=world_minute,
                scheduled_departure=first_scheduled,
                boarding_grace_minutes=first_grace,
            ),
            boarding_grace_minutes=first_grace,
            route_status=_aggregate_route_status(statuses),
            route_statuses=statuses,
            danger=danger,
            max_leg_danger=max(route_dangers, default=0),
            route_ids=route_ids,
            leg_departure_minutes=actual_departures,
            leg_arrival_minutes=leg_arrivals,
        ))
    return sorted(
        destinations,
        key=lambda item: (item.journey_minutes, item.fare, item.name),
    )


async def resolve_carriage_travel(
    session: AsyncSession,
    *,
    from_room_id: int,
    destination_stop_id: object,
    world_minute: int | None = None,
    boarding_grace_minutes: int | None = None,
) -> CarriageDestination:
    if (
        isinstance(destination_stop_id, bool)
        or not isinstance(destination_stop_id, int)
    ):
        raise CarriageError("Choose a real carriage stop.")
    source = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == from_room_id)
    )).scalars().first()
    if source is None or source.status != "operating":
        raise CarriageError("No operating carriage leaves from here.")
    destinations = await reachable_destinations(
        session,
        source.id,
        world_minute=world_minute,
        boarding_grace_minutes=boarding_grace_minutes,
        require_boardable_now=world_minute is not None,
    )
    destination = next(
        (item for item in destinations if item.stop_id == destination_stop_id),
        None,
    )
    if destination is None:
        if world_minute is not None:
            future_destinations = await reachable_destinations(
                session,
                source.id,
                world_minute=world_minute,
                boarding_grace_minutes=boarding_grace_minutes,
            )
            future = next(
                (
                    item
                    for item in future_destinations
                    if item.stop_id == destination_stop_id
                ),
                None,
            )
            if future is not None:
                departure = future.next_departure_minute
                clock = (
                    _clock_text(departure)
                    if departure is not None
                    else "on demand"
                )
                raise CarriageError(
                    "That carriage is not boarding yet. "
                    f"The next service is in {future.wait_minutes} minutes "
                    f"({clock})."
                )
        raise CarriageError("No known carriage route reaches that stop.")
    return destination


async def _connect_new_stop(
    session: AsyncSession,
    stop: CarriageStop,
) -> None:
    """Connect a newly named stop to up to three nearest operating stops."""
    others = (await session.execute(
        select(CarriageStop).where(
            CarriageStop.id != stop.id,
            CarriageStop.status == "operating",
        )
    )).scalars().all()
    others = [
        other
        for other in others
        if not isinstance(other.details, dict)
        or other.details.get("accepts_generated_routes", True) is not False
    ]
    if not others:
        return
    stop_depth = await _stop_depth(session, stop)
    # Async depth lookup must happen outside the sort key.
    depth_by_id = {other.id: await _stop_depth(session, other) for other in others}
    ranked = sorted(
        others,
        key=lambda other: (
            abs(depth_by_id[other.id] - stop_depth),
            other.id,
        ),
    )[:3]
    for other in ranked:
        distance = max(1, abs(depth_by_id[other.id] - stop_depth))
        minutes = 20 + distance * 12
        fare = 2 + distance
        danger = max(0, distance - 2)
        for source, target in ((stop, other), (other, stop)):
            route_key = f"{source.stop_key}->{target.stop_key}"
            existing = (await session.execute(
                select(CarriageRoute).where(
                    CarriageRoute.route_key == route_key
                )
            )).scalars().first()
            if existing is None:
                session.add(CarriageRoute(
                    route_key=route_key,
                    from_stop_id=source.id,
                    to_stop_id=target.id,
                    travel_minutes=minutes,
                    fare=fare,
                    danger=danger,
                    status="operating",
                    departures=[360, 720, 1080],
                    details={"community_route": True},
                ))


async def _stop_depth(session: AsyncSession, stop: CarriageStop) -> int:
    node = (await session.execute(
        select(FrontierNode).where(FrontierNode.room_id == stop.room_id)
    )).scalars().first()
    return node.depth if node is not None else 0


def _validate_schedule_options(
    *,
    world_minute: int | None,
    boarding_grace_minutes: int | None,
) -> None:
    if world_minute is not None and (
        isinstance(world_minute, bool)
        or not isinstance(world_minute, int)
        or world_minute < 0
    ):
        raise CarriageError("The carriage clock is not readable.")
    if boarding_grace_minutes is not None and (
        isinstance(boarding_grace_minutes, bool)
        or not isinstance(boarding_grace_minutes, int)
        or not 0 <= boarding_grace_minutes <= 60
    ):
        raise CarriageError("Boarding grace must be between 0 and 60 minutes.")


def _route_departures(route: CarriageRoute) -> tuple[int, ...]:
    """Return safe persisted minutes-of-day, or empty for on-demand service."""
    values = route.departures if isinstance(route.departures, list) else []
    return tuple(sorted({
        value
        for value in values
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value < MINUTES_PER_DAY
        )
    }))


def _route_service_days(route: CarriageRoute) -> tuple[int, ...] | None:
    """Return optional Monday-first weekdays for an authored service.

    Community routes omit this metadata and retain their original daily
    recurrence. Authored routes use lowercase weekday names so their persisted
    schedule stays legible beside the living-world source document.
    """
    details = route.details if isinstance(route.details, dict) else {}
    raw = details.get("service_days")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return ()
    day_indexes = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    parsed = {
        day_indexes[value]
        for value in raw
        if isinstance(value, str) and value in day_indexes
    }
    return tuple(sorted(parsed))


def _route_boarding_grace(
    route: CarriageRoute,
    *,
    override: int | None,
) -> int:
    if override is not None:
        return override
    details = route.details if isinstance(route.details, dict) else {}
    value = details.get(
        "boarding_grace_minutes",
        DEFAULT_BOARDING_GRACE_MINUTES,
    )
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 60
    ):
        return value
    return DEFAULT_BOARDING_GRACE_MINUTES


def _route_embedded_layover(route: CarriageRoute) -> int:
    """Return dwell time folded into a terminal-alias route's duration."""
    details = route.details if isinstance(route.details, dict) else {}
    value = details.get("layover_minutes", 0)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value < route.travel_minutes
    ):
        return value
    return 0


def _route_delay(route: CarriageRoute) -> int:
    """A delayed route may publish a deterministic offset in its details."""
    if route.status != "delayed":
        return 0
    details = route.details if isinstance(route.details, dict) else {}
    value = details.get("delay_minutes", 0)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MINUTES_PER_DAY
    ):
        return value
    return 0


def _next_route_departure(
    route: CarriageRoute,
    *,
    ready_minute: int,
    boarding_grace_minutes: int,
) -> tuple[int | None, int, int]:
    """Find the first daily service this arrival can still board.

    The scheduled minute remains visible even if the coach is holding within
    its grace period. In that case the effective boarding minute is "now";
    arriving early waits until the published departure.
    """
    departures = _route_departures(route)
    if not departures:
        return None, ready_minute, 0

    delay = _route_delay(route)
    service_days = _route_service_days(route)
    first_day = max(
        0,
        (ready_minute - delay) // MINUTES_PER_DAY - 1,
    )
    # Eight days covers the previous/current day and a complete future week.
    # Daily community services normally resolve on the first two iterations.
    for day in range(first_day, first_day + 9):
        if service_days is not None and day % 7 not in service_days:
            continue
        for minute_of_day in departures:
            scheduled = day * MINUTES_PER_DAY + minute_of_day + delay
            if ready_minute <= scheduled + boarding_grace_minutes:
                actual = max(ready_minute, scheduled)
                return scheduled, actual, max(0, scheduled - ready_minute)
    # The finite scan above always reaches a recurrence, but retaining a
    # deterministic fallback makes corrupt or extreme persisted data safe.
    scheduled = (
        (first_day + 9) * MINUTES_PER_DAY
        + departures[0]
        + delay
    )
    return scheduled, scheduled, scheduled - ready_minute


def _is_boardable_now(
    *,
    world_minute: int | None,
    scheduled_departure: int | None,
    boarding_grace_minutes: int,
) -> bool:
    # ``None`` world time is the explicit compatibility mode. A route without
    # departures is explicitly on-demand.
    if world_minute is None or scheduled_departure is None:
        return True
    return (
        scheduled_departure - boarding_grace_minutes
        <= world_minute
        <= scheduled_departure + boarding_grace_minutes
    )


def _aggregate_route_status(statuses: tuple[str, ...]) -> str:
    distinct = {status for status in statuses if status != "operating"}
    if not distinct:
        return "operating"
    if len(distinct) == 1:
        return next(iter(distinct))
    return "mixed"


def _clock_text(world_minute: int) -> str:
    minute_of_day = world_minute % MINUTES_PER_DAY
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def _normalize_name(value: object) -> str:
    if not isinstance(value, str):
        raise CarriageError("A stop name must be text.")
    name = " ".join(value.strip().split())
    if not (2 <= len(name) <= CARRIAGE_STOP_NAME_LIMIT):
        raise CarriageError(
            f"Stop names must be 2–{CARRIAGE_STOP_NAME_LIMIT} characters."
        )
    if not _VALID_NAME.fullmatch(name):
        raise CarriageError(
            "Use letters, numbers, spaces, apostrophes, or hyphens."
        )
    return name


def _stop_view(stop: CarriageStop) -> dict:
    return {
        "id": stop.id,
        "name": stop.public_name or "Unnamed Waystop",
        "room_id": stop.room_id,
        "biome": stop.biome,
        "status": stop.status,
        "community_named": stop.named_by_player_id is not None,
    }


def should_create_frontier_stop(
    *,
    world_seed: int,
    node_key: str,
    depth: int,
    archetype: str,
) -> bool:
    """About one in six rooms, with caravan remains strongly favoured."""
    if archetype == "caravan_remains":
        return True
    if depth < 2:
        return False
    digest = hashlib.blake2b(
        f"{world_seed}:{node_key}:carriage".encode("utf-8"),
        digest_size=2,
    ).digest()
    return int.from_bytes(digest, "big") / 65535 < 0.16
