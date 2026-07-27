"""Shared, community-named carriage-stop network."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
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

    def to_dict(self) -> dict:
        return {
            "stop_id": self.stop_id,
            "name": self.name,
            "room_id": self.room_id,
            "travel_minutes": self.travel_minutes,
            "fare": self.fare,
            "route_stop_ids": list(self.route_stop_ids),
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
    destinations = await reachable_destinations(session, stop.id)
    await session.commit()
    return {
        "stop": _stop_view(stop),
        "destinations": [destination.to_dict() for destination in destinations],
        "can_name": stop.status == "unnamed" and stop.public_name is None,
        "name_limit": CARRIAGE_STOP_NAME_LIMIT,
    }


async def reachable_destinations(
    session: AsyncSession,
    from_stop_id: int,
) -> list[CarriageDestination]:
    stops = (await session.execute(
        select(CarriageStop).where(CarriageStop.status == "operating")
    )).scalars().all()
    stop_by_id = {stop.id: stop for stop in stops}
    if from_stop_id not in stop_by_id:
        return []
    routes = (await session.execute(
        select(CarriageRoute).where(
            CarriageRoute.status.in_(("operating", "dangerous"))
        )
    )).scalars().all()
    adjacency: dict[int, list[CarriageRoute]] = {}
    for route in routes:
        adjacency.setdefault(route.from_stop_id, []).append(route)

    # Small shared network: plain Dijkstra keeps path/fare visible to clients.
    import heapq
    queue: list[tuple[int, int, tuple[int, ...], int]] = [
        (0, 0, (from_stop_id,), from_stop_id)
    ]
    best: dict[int, tuple[int, int, tuple[int, ...]]] = {}
    while queue:
        minutes, fare, path, stop_id = heapq.heappop(queue)
        score = (minutes, fare, path)
        if stop_id in best and best[stop_id] <= score:
            continue
        best[stop_id] = score
        for route in adjacency.get(stop_id, ()):
            heapq.heappush(
                queue,
                (
                    minutes + route.travel_minutes,
                    fare + route.fare,
                    (*path, route.to_stop_id),
                    route.to_stop_id,
                ),
            )

    destinations = []
    for stop_id, (minutes, fare, path) in best.items():
        if stop_id == from_stop_id:
            continue
        stop = stop_by_id.get(stop_id)
        if stop is None or not stop.public_name:
            continue
        destinations.append(CarriageDestination(
            stop_id=stop.id,
            name=stop.public_name,
            room_id=stop.room_id,
            travel_minutes=minutes,
            fare=fare,
            route_stop_ids=path,
        ))
    return sorted(destinations, key=lambda item: (item.travel_minutes, item.name))


async def resolve_carriage_travel(
    session: AsyncSession,
    *,
    from_room_id: int,
    destination_stop_id: object,
) -> CarriageDestination:
    if not isinstance(destination_stop_id, int):
        raise CarriageError("Choose a real carriage stop.")
    source = (await session.execute(
        select(CarriageStop).where(CarriageStop.room_id == from_room_id)
    )).scalars().first()
    if source is None or source.status != "operating":
        raise CarriageError("No operating carriage leaves from here.")
    destinations = await reachable_destinations(session, source.id)
    destination = next(
        (item for item in destinations if item.stop_id == destination_stop_id),
        None,
    )
    if destination is None:
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
