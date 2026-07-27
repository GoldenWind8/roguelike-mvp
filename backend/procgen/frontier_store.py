"""Transactional materialization of persistent frontier exits."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import RNG_SEED
from backend.carriage_store import (
    activate_carriage_stop,
    ensure_carriage_stop,
    should_create_frontier_stop,
)
from backend.models import (
    FrontierExit,
    FrontierNode,
    Room,
    RoomConnection,
    WorldState,
)
from backend.procgen import generate
from backend.procgen.base import validate
from backend.procgen.frontier import (
    FrontierPressure,
    RegionCandidate,
    frontier_recipe,
    resolve_frontier_exit,
)

GENERATOR_VERSION = "frontier-v1"

_AUTHORED_GATEWAY_SPECS = (
    # Drazna is the world's first verified public rot record, so the frontier
    # strongly prefers revealing it before the more distant bell kingdom.
    ("drazna", "Drazna — the Lantern Quays", "drazna_lantern_quays", 0, 6, 4, 5),
    ("rouvray", "Rouvray — Hollow Bells Post", "hollow_bells_post", 0, 6, 7, 2),
)


@dataclass(frozen=True)
class AuthoredGateway:
    region_id: str
    label: str
    room_id: int
    reverse_x: int
    reverse_y: int
    min_depth: int = 3
    weight: int = 1
    required_fact: str | None = None


@dataclass(frozen=True)
class FrontierExpansion:
    target_room_id: int
    created_room: bool
    discovered_region_id: str | None
    label: str
    depth: int
    biome: str


async def available_authored_gateways(
    session: AsyncSession,
) -> tuple[AuthoredGateway, ...]:
    """Return seeded kingdoms not yet connected to this world's frontier."""
    discovered = set((await session.execute(
        select(FrontierNode.authored_region_id).where(
            FrontierNode.authored_region_id.is_not(None)
        )
    )).scalars().all())
    gateways: list[AuthoredGateway] = []
    for (
        region_id,
        label,
        room_content_id,
        reverse_x,
        reverse_y,
        min_depth,
        weight,
    ) in _AUTHORED_GATEWAY_SPECS:
        if region_id in discovered:
            continue
        room = (await session.execute(
            select(Room).where(Room.content_id == room_content_id)
        )).scalars().first()
        if room is None:
            continue
        gateways.append(AuthoredGateway(
            region_id=region_id,
            label=label,
            room_id=room.id,
            reverse_x=reverse_x,
            reverse_y=reverse_y,
            min_depth=min_depth,
            weight=weight,
        ))
    return tuple(gateways)


async def ensure_world_state(
    session: AsyncSession,
    *,
    world_seed: int = RNG_SEED,
    wall_now: float | None = None,
) -> WorldState:
    row = await session.get(WorldState, 1)
    if row is None:
        row = WorldState(
            id=1,
            world_seed=world_seed,
            world_minute=0,
            last_real_at=wall_now,
            revision=1,
            variables={},
        )
        session.add(row)
        await session.flush()
    return row


async def materialize_frontier_exit(
    session: AsyncSession,
    *,
    source_room_id: int,
    source_x: int,
    source_y: int,
    authored_gateways: tuple[AuthoredGateway, ...] = (),
    known_facts: frozenset[str] = frozenset(),
) -> FrontierExpansion:
    """Resolve one frontier door to a generated room or authored gateway.

    The caller serializes this operation at the application boundary. This
    function still checks persistent state first, so retries after a dropped
    websocket return the already-created destination instead of growing a
    duplicate branch.
    """
    exit_row = (await session.execute(
        select(FrontierExit).where(
            FrontierExit.source_room_id == source_room_id,
            FrontierExit.source_x == source_x,
            FrontierExit.source_y == source_y,
        )
    )).scalars().first()
    if exit_row is None or exit_row.status == "sealed":
        raise ValueError("There is no open frontier at that tile.")
    if exit_row.status == "connected" and exit_row.target_room_id is not None:
        node = (await session.execute(
            select(FrontierNode).where(
                FrontierNode.room_id == exit_row.target_room_id
            )
        )).scalars().first()
        target = await session.get(Room, exit_row.target_room_id)
        return FrontierExpansion(
            target_room_id=exit_row.target_room_id,
            created_room=False,
            discovered_region_id=node.authored_region_id if node else None,
            label=target.name if target else "The discovered road",
            depth=node.depth if node else 0,
            biome=node.biome if node else (exit_row.biome_hint or "amberfall_fields"),
        )

    source_room = await session.get(Room, source_room_id)
    if source_room is None:
        raise ValueError("The frontier's source room no longer exists.")
    source_node = (await session.execute(
        select(FrontierNode).where(FrontierNode.room_id == source_room_id)
    )).scalars().first()
    depth = (source_node.depth + 1) if source_node else 1
    biome = (
        exit_row.biome_hint
        or (source_node.biome if source_node else None)
        or "amberfall_fields"
    )
    world = await ensure_world_state(session)
    pressure = FrontierPressure(misses=int(exit_row.discovery_pressure))
    region_candidates = tuple(
        RegionCandidate(
            gateway.region_id,
            gateway.label,
            min_depth=gateway.min_depth,
            base_weight=gateway.weight,
            required_fact=gateway.required_fact,
        )
        for gateway in authored_gateways
    )
    exit_key = f"{source_room_id}:{source_x}:{source_y}"
    outcome = resolve_frontier_exit(
        world_seed=world.world_seed,
        exit_key=exit_key,
        depth=depth,
        pressure=pressure,
        candidates=region_candidates,
        known_facts=known_facts,
    )

    if outcome.kind == "authored_region":
        gateway = next(
            gateway
            for gateway in authored_gateways
            if gateway.region_id == outcome.region_id
        )
        target = await session.get(Room, gateway.room_id)
        if target is None:
            raise ValueError(f"authored gateway {gateway.region_id!r} is unavailable")
        await _connect_rooms(
            session,
            source_room_id=source_room_id,
            source_x=source_x,
            source_y=source_y,
            target_room_id=target.id,
            reverse_x=gateway.reverse_x,
            reverse_y=gateway.reverse_y,
        )
        node = (await session.execute(
            select(FrontierNode).where(FrontierNode.room_id == target.id)
        )).scalars().first()
        if node is None:
            session.add(FrontierNode(
                node_key=f"authored:{gateway.region_id}",
                room_id=target.id,
                world_seed=world.world_seed,
                generation_seed=0,
                depth=depth,
                biome=biome,
                generator_kind="authored",
                generator_version="1",
                generator_params={},
                content={},
                generation_metadata={"entered_from": exit_key},
                authored_region_id=gateway.region_id,
                discovered_at_minute=world.world_minute,
            ))
        _resolve_exit_row(
            exit_row,
            target_room_id=target.id,
            world_minute=world.world_minute,
        )
        await activate_carriage_stop(session, room_id=target.id)
        await session.commit()
        return FrontierExpansion(
            target_room_id=target.id,
            created_room=False,
            discovered_region_id=gateway.region_id,
            label=gateway.label,
            depth=depth,
            biome=biome,
        )

    node_key = _node_key(world.world_seed, exit_key, exit_row.attempt_count)
    generation_seed = _stable_i32(
        world.world_seed,
        exit_row.roll_seed,
        exit_row.attempt_count,
        depth,
    )
    recipe = frontier_recipe(
        world_seed=world.world_seed,
        node_key=node_key,
        depth=depth,
        biome=biome,
    )
    result = generate(recipe.preset, recipe.params, generation_seed)
    if not result.ok or result.room is None:
        exit_row.attempt_count += 1
        exit_row.last_attempt_minute = world.world_minute
        await session.commit()
        raise ValueError(result.error or "The frontier could not be formed.")

    data = result.room
    creates_waystop = should_create_frontier_stop(
        world_seed=world.world_seed,
        node_key=node_key,
        depth=depth,
        archetype=str(recipe.params["archetype"]),
    )
    if creates_waystop:
        _add_waystop_object(data, node_key)
        error = validate(data)
        if error is not None:
            raise ValueError(f"generated carriage waystop is invalid: {error}")
    target = Room(
        content_id=None,
        name=data["name"],
        width=data["width"],
        height=data["height"],
        terrain=data["terrain"],
        objects=data.get("objects", []),
        spawn_points=data["spawn_points"],
        enemy_spawns=data.get("enemy_spawns", []),
    )
    session.add(target)
    await session.flush()

    entries = _entry_tiles(data)
    reverse = min(
        entries,
        key=lambda tile: (
            min(
                max(abs(tile[0] - sx), abs(tile[1] - sy))
                for sx, sy in data["spawn_points"]
            ),
            tile[1],
            tile[0],
        ),
    )
    await _connect_rooms(
        session,
        source_room_id=source_room_id,
        source_x=source_x,
        source_y=source_y,
        target_room_id=target.id,
        reverse_x=reverse[0],
        reverse_y=reverse[1],
    )
    session.add(FrontierNode(
        node_key=node_key,
        room_id=target.id,
        world_seed=world.world_seed,
        generation_seed=generation_seed,
        depth=depth,
        biome=biome,
        generator_kind=recipe.preset,
        generator_version=GENERATOR_VERSION,
        generator_params=result.params,
        content={
            "mood_tags": list(recipe.mood_tags),
            "encounter_tags": list(recipe.encounter_tags),
        },
        generation_metadata={
            "source_exit": exit_key,
            "roll": outcome.roll,
            "region_chance": outcome.chance,
            "attempts": result.attempts,
        },
        authored_region_id=None,
        discovered_at_minute=world.world_minute,
    ))
    if creates_waystop:
        await ensure_carriage_stop(
            session,
            stop_key=f"stop:{node_key}",
            room_id=target.id,
            biome=biome,
            world_minute=world.world_minute,
            metadata={
                "generated": True,
                "node_key": node_key,
                "archetype": recipe.params["archetype"],
            },
        )

    inherited_pressure = outcome.next_pressure.misses
    for x, y in entries:
        if (x, y) == reverse:
            continue
        session.add(FrontierExit(
            source_room_id=target.id,
            source_x=x,
            source_y=y,
            status="frontier",
            target_room_id=None,
            discovery_pressure=float(inherited_pressure),
            attempt_count=0,
            roll_seed=_stable_i32(world.world_seed, node_key, x, y),
            biome_hint=_next_biome(biome, depth, x, y),
            generator_hint={
                "parent_node": node_key,
                "mood_tags": list(recipe.mood_tags),
            },
            created_at_minute=world.world_minute,
        ))

    _resolve_exit_row(
        exit_row,
        target_room_id=target.id,
        world_minute=world.world_minute,
    )
    await session.commit()
    return FrontierExpansion(
        target_room_id=target.id,
        created_room=True,
        discovered_region_id=None,
        label=target.name,
        depth=depth,
        biome=biome,
    )


def _resolve_exit_row(
    row: FrontierExit,
    *,
    target_room_id: int,
    world_minute: int,
) -> None:
    row.status = "connected"
    row.target_room_id = target_room_id
    row.attempt_count += 1
    row.last_attempt_minute = world_minute


async def _connect_rooms(
    session: AsyncSession,
    *,
    source_room_id: int,
    source_x: int,
    source_y: int,
    target_room_id: int,
    reverse_x: int,
    reverse_y: int,
) -> None:
    for from_id, to_id, x, y in (
        (source_room_id, target_room_id, source_x, source_y),
        (target_room_id, source_room_id, reverse_x, reverse_y),
    ):
        existing = (await session.execute(
            select(RoomConnection).where(
                RoomConnection.from_room_id == from_id,
                RoomConnection.from_x == x,
                RoomConnection.from_y == y,
            )
        )).scalars().first()
        if existing is None:
            session.add(RoomConnection(
                from_room_id=from_id,
                to_room_id=to_id,
                from_x=x,
                from_y=y,
            ))
        else:
            existing.to_room_id = to_id


def _entry_tiles(room: dict) -> list[tuple[int, int]]:
    entries = [
        (x, y)
        for y, row in enumerate(room["terrain"])
        for x, tile in enumerate(row)
        if tile in "+O"
    ]
    if not entries:
        raise ValueError("generated frontier room has no entries")
    return entries


def _add_waystop_object(room: dict, node_key: str) -> None:
    occupied = {
        (x, y)
        for x, y in room.get("spawn_points", [])
    } | {
        (enemy["x"], enemy["y"])
        for enemy in room.get("enemy_spawns", [])
    } | {
        (obj["x"], obj["y"])
        for obj in room.get("objects", [])
    }
    entries = set(_entry_tiles(room))
    candidates = [
        (x, y)
        for y, row in enumerate(room["terrain"])
        for x, tile in enumerate(row)
        if tile == "."
        and (x, y) not in occupied
        and all(abs(x - ex) + abs(y - ey) >= 3 for ex, ey in entries)
    ]
    if not candidates:
        raise ValueError("generated room has no tile for a carriage waystop")
    x, y = min(
        candidates,
        key=lambda tile: (
            abs(tile[0] - room["width"] // 2)
            + abs(tile[1] - room["height"] // 2),
            tile[1],
            tile[0],
        ),
    )
    room.setdefault("objects", []).append({
        "id": f"waystop_{node_key.removeprefix('frontier:')}",
        "type": "frontier_waystop",
        "x": x,
        "y": y,
    })


def _node_key(world_seed: int, exit_key: str, attempt: int) -> str:
    digest = hashlib.blake2b(
        f"{world_seed}:{exit_key}:{attempt}".encode("utf-8"),
        digest_size=8,
    ).hexdigest()
    return f"frontier:{digest}"


def _stable_i32(*parts: object) -> int:
    digest = hashlib.blake2b(
        "\x1f".join(str(part) for part in parts).encode("utf-8"),
        digest_size=4,
    ).digest()
    # SQLite INTEGER and Python's Random both handle this positive range.
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF


def _next_biome(current: str, depth: int, x: int, y: int) -> str:
    # A frontier gradually takes on the character of the regions it might
    # reveal, while branches can diverge.
    if current == "amberfall_fields" and depth >= 5:
        return "rouvray_lowlands" if (x + y) % 2 else "drazna_marches"
    if depth >= 10:
        return "deep_frontier"
    return current
