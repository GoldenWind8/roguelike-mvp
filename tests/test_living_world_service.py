"""Database-backed integration tests for the dormant NPC simulation."""

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select

from backend.living_world.memory import Memory, synthesize_reflection
from backend.living_world.service import (
    LivingWorldConfig,
    LivingWorldService,
)
from backend.living_world.store import (
    epoch_datetime,
    memory_from_row,
    memory_rows,
    reflection_source_rows,
    remember_once,
    roommates,
    retrieve_memory_rows,
)
from backend.models import (
    NPCGoal,
    NPCMemory,
    NPCRelationship,
    NPCRow,
    Room,
    RoomConnection,
    ScheduledWorldEvent,
    WorldEvent,
    WorldState,
)
from backend.seeds import (
    DEFAULT_ROOM,
    SECOND_ROOM,
    get_or_seed_default_room,
    seed_default_rooms,
)
from backend.npc_store import load_npcs


@dataclass
class FakeContent:
    npc_profiles: dict = field(default_factory=dict)
    rumors: dict = field(default_factory=dict)
    kingdoms: dict = field(default_factory=dict)


def _config(**overrides) -> LivingWorldConfig:
    values = {
        "game_minutes_per_real_minute": 1.0,
        "catchup_cap_minutes": 2_000,
        "room_edge_travel_minutes": 10,
        "max_events_per_advance": 2_000,
        "max_conversations_per_advance": 8,
        "max_conversation_turns": 4,
        "max_rumour_cascade_depth": 3,
        "memories_per_conversation_turn": 1,
        "world_seed": 991,
    }
    values.update(overrides)
    return LivingWorldConfig(**values)


def _profile(
    npc_id: str,
    *,
    home: str,
    windows=(10, 500, 1_000),
    schedule_commitment=0,
    target_kind="self",
    target_id=None,
    goal_priority=5,
) -> dict:
    return {
        "id": npc_id,
        "deliberation_windows": [
            {"minute": minute, "purpose": "replan"}
            for minute in windows
        ],
        "schedule": [{
            "start_minute": 0,
            "location_id": home,
            "activity": "work",
            "commitment": schedule_commitment,
        }],
        "private_goals": [{
            "id": "private-direction",
            "desire": "Follow a private concern without waiting for a player.",
            "priority": goal_priority,
            "approach": "patient",
            "target": {
                "kind": target_kind,
                "id": target_id or npc_id,
            },
            "risk_tolerance": "measured",
        }],
        "offscreen_policy": {
            "can_relocate": True,
            "can_die": True,
            "missed_windows_are_final": True,
            "minimum_warning_memories": 1,
        },
    }


async def _prototype_world(session):
    hall = await seed_default_rooms(session)
    ante = (await session.execute(
        select(Room).where(Room.name == SECOND_ROOM["name"])
    )).scalar_one()
    hall.content_id = "test-hall"
    ante.content_id = "test-ante"
    await session.commit()
    gorrik = (await session.execute(
        select(NPCRow).where(NPCRow.name == "Gorrik")
    )).scalar_one()
    mara = (await session.execute(
        select(NPCRow).where(NPCRow.name == "Mara")
    )).scalar_one()
    return hall, ante, gorrik, mara


async def _clock_at_zero(session):
    session.add(WorldState(
        id=1,
        world_seed=991,
        world_minute=0,
        last_real_at=epoch_datetime(0),
        revision=0,
        variables={},
    ))
    await session.commit()


async def test_bounded_clock_catchup_is_durable_and_idempotent(session):
    await _clock_at_zero(session)
    service = LivingWorldService(
        config=_config(catchup_cap_minutes=120),
        content=FakeContent(),
    )

    first = await service.advance(
        session,
        wall_now=200 * 60,
        active_room_ids=(),
    )
    assert first.from_minute == 0
    assert first.to_minute == 120
    assert first.simulated_minutes == 120
    assert first.coalesced_minutes == 80

    state = await session.get(WorldState, 1)
    revision = state.revision
    quiet_count = (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.kind == "quiet_interval"
        )
    )).scalar_one()

    replay = await service.advance(
        session,
        wall_now=200 * 60,
        active_room_ids=(),
    )
    assert replay.simulated_minutes == 0
    assert replay.processed_events == 0
    assert (await session.get(WorldState, 1)).revision == revision
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.kind == "quiet_interval"
        )
    )).scalar_one() == quiet_count == 1


async def test_explicit_journey_advances_full_time_without_consuming_wall_clock(session):
    await _clock_at_zero(session)
    service = LivingWorldService(
        config=_config(catchup_cap_minutes=120),
        content=FakeContent(),
    )
    before = (await session.get(WorldState, 1)).last_real_at

    journey = await service.advance(
        session,
        wall_now=0,
        active_room_ids=(),
        forced_minutes=24 * 60,
    )

    state = await session.get(WorldState, 1)
    assert journey.from_minute == 0
    assert journey.to_minute == 24 * 60
    assert journey.simulated_minutes == 24 * 60
    assert journey.coalesced_minutes == 0
    assert state.last_real_at == before


async def test_profileless_npc_deliberates_only_three_to_six_times_per_day(session):
    _hall, _ante, gorrik, _mara = await _prototype_world(session)
    await _clock_at_zero(session)
    service = LivingWorldService(
        config=_config(catchup_cap_minutes=MINUTES_IN_DAY),
        content=FakeContent(),
    )

    await service.advance(
        session,
        wall_now=MINUTES_IN_DAY * 60,
        active_room_ids=(),
    )

    deliberations = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "npc_deliberated",
            WorldEvent.actor_id == gorrik.content_id,
            WorldEvent.world_minute <= MINUTES_IN_DAY,
        )
    )).scalars().all()
    assert 3 <= len(deliberations) <= 6
    assert len({event.world_minute for event in deliberations}) == len(deliberations)


MINUTES_IN_DAY = 24 * 60


async def test_general_intention_becomes_programmatic_persistent_travel(session):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    await _clock_at_zero(session)
    content = FakeContent(npc_profiles={
        gorrik.content_id: _profile(
            gorrik.content_id,
            home="test-ante",
            target_kind="location",
            target_id="test-hall",
        ),
    })
    service = LivingWorldService(config=_config(), content=content)

    first = await service.advance(
        session,
        wall_now=25 * 60,
        active_room_ids=(),
    )
    session.expunge_all()
    moved = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == gorrik.content_id)
    )).scalar_one()

    assert first.deliberations >= 1
    assert first.movements == 1
    assert moved.room_id == hall.id
    assert moved.room_id != ante.id
    assert (moved.x, moved.y) not in {
        tuple(point) for point in hall.spawn_points
    }
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.kind == "npc_began_travel",
            WorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one() == 1
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.kind == "npc_arrived_room",
            WorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one() == 1
    assert (await session.execute(
        select(func.count()).select_from(NPCMemory).where(
            NPCMemory.npc_content_id == gorrik.content_id,
            NPCMemory.kind.in_(("plan", "outcome")),
        )
    )).scalar_one() == 2

    event_count = (await session.execute(
        select(func.count()).select_from(ScheduledWorldEvent)
    )).scalar_one()
    replay = await LivingWorldService(
        config=_config(), content=content,
    ).advance(session, wall_now=25 * 60, active_room_ids=())
    assert replay.simulated_minutes == 0
    assert replay.movements == 0
    assert (await session.execute(
        select(func.count()).select_from(ScheduledWorldEvent)
    )).scalar_one() == event_count
    assert (await session.execute(
        select(NPCRow.room_id).where(NPCRow.content_id == gorrik.content_id)
    )).scalar_one() == hall.id
    assert [
        npc.db_id
        for npc in await load_npcs(session, hall.id)
        if npc.db_id == moved.id
    ] == [moved.id]
    assert [
        npc.content_id
        for npc in await roommates(session, room_id=hall.id)
        if npc.content_id == moved.content_id
    ] == [moved.content_id]


async def test_edgewise_traveller_stays_hidden_at_intermediate_arrival(session):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    far_room = Room(
        content_id="test-edgewise-far-room",
        name="Edgewise Far Room",
        width=5,
        height=5,
        terrain=["#####", "#...#", "+...#", "#...#", "#####"],
        objects=[],
        spawn_points=[[1, 1]],
        enemy_spawns=[],
    )
    session.add(far_room)
    await session.flush()
    session.add_all((
        RoomConnection(
            from_room_id=hall.id,
            to_room_id=far_room.id,
            from_x=hall.width - 1,
            from_y=2,
        ),
        RoomConnection(
            from_room_id=far_room.id,
            to_room_id=hall.id,
            from_x=0,
            from_y=2,
        ),
    ))
    gorrik.room_id = ante.id
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        target_kind="location",
        target_id=far_room.content_id,
    )
    service = LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    )

    first_leg = await service.advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(),
    )
    await session.refresh(gorrik)
    assert first_leg.movements == 1
    assert gorrik.room_id == hall.id
    pending = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.actor_id == gorrik.content_id,
            ScheduledWorldEvent.kind == "npc_arrive_room",
            ScheduledWorldEvent.status == "pending",
        )
    )).scalar_one()
    assert pending.payload["step_index"] == 2
    assert all(
        npc.db_id != gorrik.id
        for npc in await load_npcs(session, hall.id)
    )
    assert all(
        npc.content_id != gorrik.content_id
        for npc in await roommates(session, room_id=hall.id)
    )

    final_leg = await service.advance(
        session,
        wall_now=30 * 60,
        active_room_ids=(),
    )
    await session.refresh(gorrik)
    assert final_leg.movements == 1
    assert gorrik.room_id == far_room.id
    assert [
        npc.db_id
        for npc in await load_npcs(session, far_room.id)
        if npc.db_id == gorrik.id
    ] == [gorrik.id]
    assert [
        npc.content_id
        for npc in await roommates(session, room_id=far_room.id)
        if npc.content_id == gorrik.content_id
    ] == [gorrik.content_id]


async def test_schedule_travel_uses_arrival_without_plan_chatter(session):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = ante.id
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
        target_kind="self",
        goal_priority=1,
    )
    profile["schedule"] = [
        {
            "start_minute": 0,
            "location_id": "test-ante",
            "activity": "sleep",
            "commitment": 90,
        },
        {
            "start_minute": 300,
            "location_id": "test-hall",
            "activity": "work",
            "commitment": 100,
        },
    ]

    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    ).advance(
        session,
        wall_now=320 * 60,
        active_room_ids=(),
    )

    await session.refresh(gorrik)
    assert gorrik.room_id == hall.id
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.actor_id == gorrik.content_id,
            WorldEvent.kind == "npc_arrived_room",
        )
    )).scalar_one() == 0
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.actor_id == gorrik.content_id,
            WorldEvent.kind == "npc_began_travel",
        )
    )).scalar_one() == 0
    assert (await session.execute(
        select(func.count()).select_from(NPCMemory).where(
            NPCMemory.npc_content_id == gorrik.content_id,
            NPCMemory.kind.in_(("plan", "outcome")),
        )
    )).scalar_one() == 0
    arrival = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_arrive_room",
            ScheduledWorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one()
    assert arrival.status == "resolved"
    assert arrival.payload["coalesced_schedule"] is True
    assert arrival.payload["coalesced"] is True
    assert [
        npc.db_id
        for npc in await load_npcs(session, hall.id)
        if npc.db_id == gorrik.id
    ] == [gorrik.id]
    assert [
        npc.content_id
        for npc in await roommates(session, room_id=hall.id)
        if npc.content_id == gorrik.content_id
    ] == [gorrik.content_id]


async def test_coalesced_schedule_route_defers_and_revalidates_without_chatter(
    session,
):
    hall, ante, gorrik, mara = await _prototype_world(session)
    far_room = Room(
        content_id="test-far-schedule",
        name="Far Schedule",
        width=5,
        height=5,
        terrain=["#####", "#...#", "+...#", "#...#", "#####"],
        objects=[],
        spawn_points=[[1, 1]],
        enemy_spawns=[],
    )
    session.add(far_room)
    await session.flush()
    session.add_all((
        RoomConnection(
            from_room_id=hall.id,
            to_room_id=far_room.id,
            from_x=hall.width - 1,
            from_y=2,
        ),
        RoomConnection(
            from_room_id=far_room.id,
            to_room_id=hall.id,
            from_x=0,
            from_y=2,
        ),
    ))
    gorrik.room_id = ante.id
    mara.room_id = ante.id
    mara.x, mara.y = 2, 2
    await session.commit()
    await remember_once(
        session,
        Memory(
            id="test-shareable-before-commute",
            owner_id=gorrik.content_id,
            kind="observation",
            summary="A piece of road-talk waits to be shared.",
            tags=frozenset({"road"}),
            importance=3,
            confidence=1,
            occurred_at=0,
            shareable=True,
        ),
    )
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 310, 1_200),
        target_kind="self",
        goal_priority=1,
    )
    profile["schedule"] = [
        {
            "start_minute": 0,
            "location_id": "test-ante",
            "activity": "sleep",
            "commitment": 90,
        },
        {
            "start_minute": 300,
            "location_id": "test-far-schedule",
            "activity": "work",
            "commitment": 100,
        },
    ]
    service = LivingWorldService(
        config=_config(),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    )

    await service.advance(
        session,
        wall_now=315 * 60,
        active_room_ids=(hall.id,),
    )
    await session.refresh(gorrik)
    assert gorrik.room_id == ante.id
    arrival = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_arrive_room",
            ScheduledWorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one()
    assert arrival.due_minute == 320
    assert arrival.status == "pending"
    assert arrival.payload["coalesced_schedule"] is True
    assert arrival.payload["route_room_ids"] == [
        ante.id,
        hall.id,
        far_room.id,
    ]
    assert (await session.execute(
        select(func.count()).select_from(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_conversation",
            ScheduledWorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one() == 0
    second_thought = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "npc_deliberated",
            WorldEvent.actor_id == gorrik.content_id,
            WorldEvent.world_minute == 310,
        )
    )).scalar_one()
    assert second_thought.payload["in_transit"] is True

    deferred = await service.advance(
        session,
        wall_now=325 * 60,
        active_room_ids=(hall.id,),
    )
    assert deferred.skipped_active_events >= 1
    await session.refresh(gorrik)
    assert gorrik.room_id == ante.id
    assert arrival.status == "pending"

    await session.execute(
        delete(RoomConnection).where(
            RoomConnection.from_room_id == hall.id,
            RoomConnection.to_room_id == far_room.id,
        )
    )
    await session.commit()
    resumed = await service.advance(
        session,
        wall_now=325 * 60,
        active_room_ids=(),
    )
    assert resumed.simulated_minutes == 0
    assert resumed.movements == 0
    await session.refresh(arrival)
    await session.refresh(gorrik)
    assert arrival.status == "cancelled"
    assert arrival.last_error == "journey route changed before arrival"
    assert gorrik.room_id == ante.id
    assert (await session.execute(
        select(func.count()).select_from(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_arrive_room",
            ScheduledWorldEvent.actor_id == gorrik.content_id,
            ScheduledWorldEvent.status == "pending",
        )
    )).scalar_one() == 0


async def test_fresh_seed_honours_current_overnight_boundary(session):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = hall.id
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
        schedule_commitment=90,
    )
    service = LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    )

    await service.advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(),
    )

    await session.refresh(gorrik)
    assert gorrik.room_id == ante.id
    routine = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_routine_anchor",
            ScheduledWorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one()
    assert routine.due_minute == 1
    assert routine.status == "resolved"
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.kind == "npc_deliberated",
            WorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one() == 0


async def test_authored_schedule_and_goal_changes_trigger_idempotent_resync(
    session,
):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = ante.id
    await session.commit()
    await _clock_at_zero(session)
    first_profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
        target_kind="location",
        target_id="test-ante",
    )
    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: first_profile}),
    ).advance(session, wall_now=0, active_room_ids=())
    state = await session.get(WorldState, 1)
    first_signature = state.variables["living_world_sync_signature"]
    assert (await session.execute(
        select(func.count()).select_from(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_routine_anchor",
        )
    )).scalar_one() == 0

    changed_schedule = _profile(
        gorrik.content_id,
        home="test-hall",
        windows=(300, 600, 1_200),
        target_kind="location",
        target_id="test-ante",
    )
    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={
            gorrik.content_id: changed_schedule,
        }),
    ).advance(session, wall_now=0, active_room_ids=())
    state = await session.get(WorldState, 1)
    second_signature = state.variables["living_world_sync_signature"]
    assert second_signature != first_signature
    routine = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_routine_anchor",
        )
    )).scalar_one()
    assert routine.payload["to_room_id"] == hall.id

    changed_goal = _profile(
        gorrik.content_id,
        home="test-hall",
        windows=(300, 600, 1_200),
        target_kind="location",
        target_id="test-hall",
    )
    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: changed_goal}),
    ).advance(session, wall_now=0, active_room_ids=())
    state = await session.get(WorldState, 1)
    third_signature = state.variables["living_world_sync_signature"]
    assert third_signature != second_signature
    goal = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == gorrik.content_id,
            NPCGoal.goal_key == "private-direction",
        )
    )).scalar_one()
    assert goal.target_id == "test-hall"
    assert (await session.execute(
        select(func.count()).select_from(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_routine_anchor",
        )
    )).scalar_one() == 1

    changed_again = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
        target_kind="location",
        target_id="test-hall",
    )
    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: changed_again}),
    ).advance(session, wall_now=0, active_room_ids=())
    state = await session.get(WorldState, 1)
    assert state.variables["living_world_sync_signature"] != third_signature
    routine = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_routine_anchor",
        )
    )).scalar_one()
    assert routine.status == "cancelled"
    assert routine.last_error == "authored overnight anchor changed"


async def test_routine_commitments_reclaim_overnight_home_without_extra_thoughts(
    session,
):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
        target_kind="location",
        target_id="test-hall",
        goal_priority=5,
    )
    profile["schedule"] = [
        {
            "start_minute": 0,
            "location_id": "test-ante",
            "activity": "sleep",
            "commitment": 70,
        },
        {
            "start_minute": 300,
            "location_id": "test-ante",
            "activity": "work",
            "commitment": 90,
        },
        {
            "start_minute": 600,
            "location_id": "test-hall",
            "activity": "socialize",
            "commitment": 20,
        },
    ]
    service = LivingWorldService(
        config=_config(
            catchup_cap_minutes=2_000,
            max_conversations_per_advance=0,
        ),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    )

    await service.advance(
        session,
        wall_now=(MINUTES_IN_DAY + 120) * 60,
        active_room_ids=(),
    )

    await session.refresh(gorrik)
    assert gorrik.room_id == ante.id
    deliberations = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "npc_deliberated",
            WorldEvent.actor_id == gorrik.content_id,
        ).order_by(WorldEvent.world_minute)
    )).scalars().all()
    assert [
        (
            event.world_minute,
            event.payload["goal_key"],
            event.payload["intention"],
        )
        for event in deliberations
    ] == [
        (300, "schedule:300:test-ante", "keep_schedule"),
        (600, "private-direction", "travel"),
        (1_200, "private-direction", "travel"),
    ]
    arrivals = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "npc_arrived_room",
            WorldEvent.actor_id == gorrik.content_id,
        )
    )).scalars().all()
    assert len(arrivals) == 1
    assert arrivals[0].room_id == hall.id
    overnight = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_routine_anchor",
            ScheduledWorldEvent.actor_id == gorrik.content_id,
            ScheduledWorldEvent.due_minute == MINUTES_IN_DAY + 1,
        )
    )).scalar_one()
    assert overnight.status == "resolved"
    assert overnight.payload["coalesced"] is True
    assert overnight.payload["from_room_id"] == hall.id
    assert overnight.payload["route_room_ids"] == [hall.id, ante.id]


async def test_routine_preserves_journey_when_home_route_is_invalid(session):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = hall.id
    await session.execute(delete(RoomConnection))
    session.add(ScheduledWorldEvent(
        dedupe_key="ordinary-trip:test-invalid-home-route",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=gorrik.content_id,
        room_id=hall.id,
        payload={
            "route_room_ids": [hall.id],
            "to_room_id": hall.id,
            "final_room_id": hall.id,
        },
    ))
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
    )

    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    ).advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(),
    )

    ordinary, routine = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.actor_id == gorrik.content_id,
            ScheduledWorldEvent.kind.in_({
                "npc_arrive_room",
                "npc_routine_anchor",
            }),
        ).order_by(ScheduledWorldEvent.kind)
    )).scalars().all()
    assert ordinary.kind == "npc_arrive_room"
    assert ordinary.status == "pending"
    assert routine.kind == "npc_routine_anchor"
    assert routine.status == "cancelled"
    assert routine.last_error == "overnight anchor has no passable room route"
    await session.refresh(gorrik)
    assert gorrik.room_id == hall.id


async def test_routine_cancels_pending_departure_while_actor_is_at_anchor(
    session,
):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = ante.id
    pending = ScheduledWorldEvent(
        dedupe_key="ordinary-trip:test-departing-after-midnight",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=gorrik.content_id,
        room_id=ante.id,
        payload={
            "route_room_ids": [ante.id, hall.id],
            "step_index": 1,
            "from_room_id": ante.id,
            "to_room_id": hall.id,
            "final_room_id": hall.id,
        },
    )
    session.add(pending)
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
    )

    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    ).advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(),
    )

    await session.refresh(gorrik)
    await session.refresh(pending)
    assert gorrik.room_id == ante.id
    assert pending.status == "cancelled"
    routine = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_routine_anchor",
            ScheduledWorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one()
    assert routine.status == "resolved"
    assert routine.payload["superseded_journey"] == pending.dedupe_key


async def test_authored_interruption_resolves_routine_across_active_return_route(
    session,
):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = hall.id
    direction = NPCGoal(
        npc_content_id=gorrik.content_id,
        goal_key="trigger-direction:active-return-route",
        kind="travel",
        target_id="test-hall",
        priority=100,
        urgency=100,
        status="active",
        created_at_minute=0,
        next_deliberation_minute=300,
        context={"authored": {
            "target_kind": "location",
            "authored_trigger": True,
        }},
    )
    session.add(direction)
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
    )

    result = await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    ).advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(ante.id,),
    )

    routine = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_routine_anchor",
            ScheduledWorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one()
    await session.refresh(gorrik)
    await session.refresh(direction)
    assert routine.status == "resolved"
    assert result.skipped_active_events == 0
    assert gorrik.room_id == hall.id
    assert direction.status == "active"


async def test_routine_defers_before_cancelling_journey_through_active_room(
    session,
):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    active_room = Room(
        content_id="test-active-route",
        name="Active Route",
        width=5,
        height=5,
        terrain=["#####", "#...#", "#...#", "#...#", "#####"],
        objects=[],
        spawn_points=[[1, 1]],
        enemy_spawns=[],
    )
    session.add(active_room)
    await session.flush()
    gorrik.room_id = hall.id
    session.add(ScheduledWorldEvent(
        dedupe_key="ordinary-trip:test-active-route",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=gorrik.content_id,
        room_id=hall.id,
        payload={
            "route_room_ids": [hall.id, active_room.id],
            "to_room_id": active_room.id,
            "final_room_id": active_room.id,
        },
    ))
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
    )
    service = LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    )

    deferred = await service.advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(active_room.id,),
    )
    assert deferred.skipped_active_events >= 1
    statuses = {
        event.kind: event.status
        for event in (await session.execute(
            select(ScheduledWorldEvent).where(
                ScheduledWorldEvent.actor_id == gorrik.content_id,
                ScheduledWorldEvent.kind.in_({
                    "npc_arrive_room",
                    "npc_routine_anchor",
                }),
            )
        )).scalars()
    }
    assert statuses == {
        "npc_arrive_room": "pending",
        "npc_routine_anchor": "pending",
    }

    resumed = await service.advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(),
    )
    assert resumed.simulated_minutes == 0
    assert resumed.movements == 1
    statuses = {
        event.kind: event.status
        for event in (await session.execute(
            select(ScheduledWorldEvent).where(
                ScheduledWorldEvent.actor_id == gorrik.content_id,
                ScheduledWorldEvent.kind.in_({
                    "npc_arrive_room",
                    "npc_routine_anchor",
                }),
            )
        )).scalars()
    }
    assert statuses == {
        "npc_arrive_room": "cancelled",
        "npc_routine_anchor": "resolved",
    }
    await session.refresh(gorrik)
    assert gorrik.room_id == ante.id


async def test_routine_ignores_active_rooms_already_traversed_by_pending_journey(
    session,
):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    past_room = Room(
        content_id="test-past-route",
        name="Past Route",
        width=5,
        height=5,
        terrain=["#####", "#...#", "#...#", "#...#", "#####"],
        objects=[],
        spawn_points=[[1, 1]],
        enemy_spawns=[],
    )
    future_room = Room(
        content_id="test-future-route",
        name="Future Route",
        width=5,
        height=5,
        terrain=["#####", "#...#", "#...#", "#...#", "#####"],
        objects=[],
        spawn_points=[[1, 1]],
        enemy_spawns=[],
    )
    session.add_all((past_room, future_room))
    await session.flush()
    gorrik.room_id = hall.id
    pending = ScheduledWorldEvent(
        dedupe_key="ordinary-trip:test-past-room-no-longer-authoritative",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=gorrik.content_id,
        room_id=hall.id,
        payload={
            "route_room_ids": [past_room.id, hall.id, future_room.id],
            "step_index": 2,
            "from_room_id": hall.id,
            "to_room_id": future_room.id,
            "final_room_id": future_room.id,
        },
    )
    session.add(pending)
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
    )

    service = LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    )
    current_deferred = await service.advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(hall.id,),
    )
    await session.refresh(pending)
    assert current_deferred.skipped_active_events >= 1
    assert pending.status == "pending"
    assert gorrik.room_id == hall.id

    result = await service.advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(past_room.id,),
    )

    await session.refresh(gorrik)
    await session.refresh(pending)
    assert result.skipped_active_events == 0
    assert pending.status == "cancelled"
    assert gorrik.room_id == ante.id


async def test_pending_traveller_is_not_loaded_or_counted_as_a_roommate(session):
    hall, ante, gorrik, mara = await _prototype_world(session)
    gorrik.room_id = ante.id
    mara.room_id = ante.id
    gorrik.x, gorrik.y = 1, 1
    mara.x, mara.y = 2, 2
    edgewise = ScheduledWorldEvent(
        dedupe_key="ordinary-trip:test-edgewise-hidden-while-in-transit",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=gorrik.content_id,
        room_id=ante.id,
        payload={
            "route_room_ids": [ante.id, hall.id],
            "step_index": 1,
            "from_room_id": ante.id,
            "to_room_id": hall.id,
            "final_room_id": hall.id,
        },
    )
    coalesced = ScheduledWorldEvent(
        dedupe_key="ordinary-trip:test-coalesced-hidden-while-in-transit",
        kind="npc_arrive_room",
        due_minute=100,
        priority=10,
        status="pending",
        actor_id=mara.content_id,
        room_id=ante.id,
        payload={
            "route_room_ids": [ante.id, hall.id],
            "step_index": 1,
            "from_room_id": ante.id,
            "to_room_id": hall.id,
            "final_room_id": hall.id,
            "coalesced_schedule": True,
        },
    )
    session.add_all((edgewise, coalesced))
    await session.commit()

    loaded_ids = [npc.db_id for npc in await load_npcs(session, ante.id)]
    roommate_ids = [
        npc.content_id
        for npc in await roommates(
            session,
            room_id=ante.id,
        )
    ]
    assert loaded_ids.count(gorrik.id) == 0
    assert loaded_ids.count(mara.id) == 0
    assert roommate_ids.count(gorrik.content_id) == 0
    assert roommate_ids.count(mara.content_id) == 0

    edgewise.status = "cancelled"
    await session.commit()
    loaded_ids = [npc.db_id for npc in await load_npcs(session, ante.id)]
    roommate_ids = [
        npc.content_id
        for npc in await roommates(
            session,
            room_id=ante.id,
        )
    ]
    assert loaded_ids.count(gorrik.id) == 1
    assert loaded_ids.count(mara.id) == 0
    assert roommate_ids.count(gorrik.content_id) == 1
    assert roommate_ids.count(mara.content_id) == 0

    coalesced.status = "cancelled"
    await session.commit()
    loaded_ids = [npc.db_id for npc in await load_npcs(session, ante.id)]
    roommate_ids = [
        npc.content_id
        for npc in await roommates(session, room_id=ante.id)
    ]
    assert loaded_ids.count(gorrik.id) == 1
    assert loaded_ids.count(mara.id) == 1
    assert roommate_ids.count(gorrik.content_id) == 1
    assert roommate_ids.count(mara.content_id) == 1


async def test_in_transit_speakers_cancel_an_already_queued_conversation(session):
    hall, ante, gorrik, mara = await _prototype_world(session)
    gorrik.room_id = ante.id
    mara.room_id = ante.id
    session.add_all((
        ScheduledWorldEvent(
            dedupe_key="ordinary-trip:test-edgewise-speaker-in-transit",
            kind="npc_arrive_room",
            due_minute=100,
            priority=10,
            status="pending",
            actor_id=gorrik.content_id,
            room_id=ante.id,
            payload={
                "route_room_ids": [ante.id, hall.id],
                "step_index": 1,
                "from_room_id": ante.id,
                "to_room_id": hall.id,
                "final_room_id": hall.id,
            },
        ),
        ScheduledWorldEvent(
            dedupe_key="ordinary-trip:test-coalesced-listener-in-transit",
            kind="npc_arrive_room",
            due_minute=100,
            priority=10,
            status="pending",
            actor_id=mara.content_id,
            room_id=ante.id,
            payload={
                "route_room_ids": [ante.id, hall.id],
                "step_index": 1,
                "from_room_id": ante.id,
                "to_room_id": hall.id,
                "final_room_id": hall.id,
                "coalesced_schedule": True,
            },
        ),
        ScheduledWorldEvent(
            dedupe_key="conversation:test-speakers-left-before-turn",
            kind="npc_conversation",
            due_minute=5,
            priority=70,
            status="pending",
            actor_id=gorrik.content_id,
            target_id=mara.content_id,
            room_id=ante.id,
            payload={
                "root_key": "conversation:test-speakers-left-before-turn",
                "turn": 1,
                "remaining_turns": 2,
                "topic_tags": ["road"],
            },
        ),
    ))
    await session.commit()
    await _clock_at_zero(session)

    result = await LivingWorldService(
        config=_config(),
        content=FakeContent(),
    ).advance(
        session,
        wall_now=5 * 60,
        active_room_ids=(),
    )

    conversation = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.dedupe_key
            == "conversation:test-speakers-left-before-turn"
        )
    )).scalar_one()
    assert result.conversations == 0
    assert conversation.status == "cancelled"
    assert conversation.attempt_count == 1
    assert conversation.last_error == "speakers are no longer together"


async def test_trigger_direction_interrupts_commitment_then_completes_on_arrival(
    session,
):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
        target_kind="self",
        goal_priority=1,
    )
    profile["schedule"] = [{
        "start_minute": 0,
        "location_id": "test-ante",
        "activity": "work",
        "commitment": 100,
    }]
    session.add(NPCGoal(
        npc_content_id=gorrik.content_id,
        goal_key="trigger-direction:test",
        kind="travel",
        target_id="test-hall",
        priority=100,
        urgency=100,
        status="active",
        created_at_minute=0,
        next_deliberation_minute=300,
        context={"authored": {
            "target_kind": "location",
            "authored_trigger": True,
        }},
    ))
    await session.commit()

    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    ).advance(
        session,
        wall_now=320 * 60,
        active_room_ids=(),
    )

    await session.refresh(gorrik)
    assert gorrik.room_id == hall.id
    direction = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.goal_key == "trigger-direction:test",
        )
    )).scalar_one()
    assert direction.status == "completed"
    assert direction.progress == 1.0
    assert direction.context["completed_at_minute"] == 310
    chosen = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "npc_deliberated",
            WorldEvent.actor_id == gorrik.content_id,
            WorldEvent.world_minute == 300,
        )
    )).scalar_one()
    assert chosen.payload["goal_key"] == "trigger-direction:test"
    assert chosen.payload["intention"] == "travel"


async def test_trigger_direction_already_at_target_completes_without_movement(
    session,
):
    _hall, ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = ante.id
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
        schedule_commitment=100,
    )
    session.add(NPCGoal(
        npc_content_id=gorrik.content_id,
        goal_key="trigger-direction:already-there",
        kind="travel",
        target_id="test-ante",
        priority=100,
        urgency=100,
        status="active",
        created_at_minute=0,
        next_deliberation_minute=300,
        context={"authored": {
            "target_kind": "location",
            "authored_trigger": True,
        }},
    ))
    await session.commit()

    result = await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    ).advance(
        session,
        wall_now=320 * 60,
        active_room_ids=(),
    )

    direction = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.goal_key == "trigger-direction:already-there",
        )
    )).scalar_one()
    assert direction.status == "completed"
    assert direction.progress == 1.0
    assert direction.context["completed_at_minute"] == 300
    assert direction.context["completed_at_room_id"] == ante.id
    assert result.movements == 0


async def test_goal_aligned_with_schedule_anchor_pays_no_commitment_cost(
    session,
):
    hall, _ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = hall.id
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-hall",
        windows=(300, 600, 1_200),
        target_kind="location",
        target_id="test-hall",
        goal_priority=5,
    )
    profile["schedule"] = [{
        "start_minute": 0,
        "location_id": "test-hall",
        "activity": "work",
        "commitment": 100,
    }]

    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    ).advance(
        session,
        wall_now=320 * 60,
        active_room_ids=(),
    )

    chosen = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "npc_deliberated",
            WorldEvent.actor_id == gorrik.content_id,
            WorldEvent.world_minute == 300,
        )
    )).scalar_one()
    assert chosen.payload["goal_key"] == "private-direction"
    assert chosen.payload["utility"] == 100


async def test_already_at_schedule_anchor_is_not_blocked_travel(session):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    gorrik.room_id = ante.id
    await session.commit()
    await _clock_at_zero(session)
    profile = _profile(
        gorrik.content_id,
        home="test-ante",
        windows=(300, 600, 1_200),
        schedule_commitment=100,
        target_kind="location",
        target_id="test-hall",
        goal_priority=1,
    )

    await LivingWorldService(
        config=_config(max_conversations_per_advance=0),
        content=FakeContent(npc_profiles={gorrik.content_id: profile}),
    ).advance(
        session,
        wall_now=320 * 60,
        active_room_ids=(),
    )

    await session.refresh(gorrik)
    assert gorrik.room_id == ante.id
    chosen = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "npc_deliberated",
            WorldEvent.actor_id == gorrik.content_id,
            WorldEvent.world_minute == 300,
        )
    )).scalar_one()
    assert chosen.payload["intention"] == "keep_schedule"
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.kind == "npc_travel_blocked",
            WorldEvent.actor_id == gorrik.content_id,
        )
    )).scalar_one() == 0


async def test_active_room_authority_defers_offscreen_action(session):
    hall, ante, gorrik, _mara = await _prototype_world(session)
    await _clock_at_zero(session)
    content = FakeContent(npc_profiles={
        gorrik.content_id: _profile(
            gorrik.content_id,
            home="test-ante",
            target_kind="location",
            target_id="test-hall",
        ),
    })
    service = LivingWorldService(config=_config(), content=content)

    skipped = await service.advance(
        session,
        wall_now=25 * 60,
        active_room_ids=(ante.id,),
    )
    assert skipped.skipped_active_events >= 1
    assert (await session.execute(
        select(NPCRow.room_id).where(NPCRow.content_id == gorrik.content_id)
    )).scalar_one() == ante.id
    pending = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.actor_id == gorrik.content_id,
            ScheduledWorldEvent.kind == "npc_deliberate",
            ScheduledWorldEvent.status == "pending",
        )
    )).scalar_one()
    assert pending.due_minute == 10

    # Once the room is dormant, the overdue private decision is allowed to
    # resolve without inventing another thought or advancing the wall clock.
    resumed = await service.advance(
        session,
        wall_now=25 * 60,
        active_room_ids=(),
    )
    assert resumed.simulated_minutes == 0
    assert resumed.movements == 1
    assert (await session.execute(
        select(NPCRow.room_id).where(NPCRow.content_id == gorrik.content_id)
    )).scalar_one() == hall.id


async def test_co_located_conversation_cascades_a_sourced_rumour_with_budget(session):
    _hall, ante, gorrik, mara = await _prototype_world(session)
    mara.room_id = ante.id
    mara.x, mara.y = 2, 2
    await session.commit()
    await _clock_at_zero(session)

    rumor_id = "rot-came-by-road"
    content = FakeContent(
        npc_profiles={
            gorrik.content_id: _profile(
                gorrik.content_id,
                home="test-ante",
                windows=(10, 500, 1_000),
            ),
            mara.content_id: _profile(
                mara.content_id,
                home="test-ante",
                windows=(12, 520, 1_020),
            ),
        },
        rumors={
            rumor_id: {
                "topic": "The rot came along the old road.",
                "truth": {
                    "classification": "unresolved",
                    "account": "The road carried witnesses as well as mud.",
                },
                "beliefs": [{
                    "id": "gorrik-road-rot",
                    "holder_npc_id": gorrik.content_id,
                    "claim": "The black mud appeared after the uncounted coach.",
                    "confidence": 85,
                    "truth_alignment": "partial",
                    "source": {
                        "kind": "firsthand",
                        "ref": "test-ante",
                        "chain": [],
                    },
                }],
                "transmission": {
                    "share_threshold": 20,
                    "distortion": "soft",
                    "contexts": ["road"],
                },
            },
        },
    )
    service = LivingWorldService(
        config=_config(
            max_conversations_per_advance=3,
            max_conversation_turns=3,
        ),
        content=content,
    )

    result = await service.advance(
        session,
        wall_now=20 * 60,
        active_room_ids=(),
    )
    mara_memories = (await session.execute(
        select(NPCMemory).where(
            NPCMemory.npc_content_id == mara.content_id,
            NPCMemory.kind == "rumour",
        )
    )).scalars().all()

    assert 1 <= result.conversations <= 3
    assert len(mara_memories) == 1
    received = mara_memories[0]
    assert received.source_id == gorrik.content_id
    assert received.source_memory_id == "belief:gorrik-road-rot"
    assert received.source_chain == [gorrik.content_id]
    assert received.cascade_depth == 1
    assert received.payload["rumor_id"] == rumor_id
    assert 0 < received.confidence < 0.85
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent).where(
            WorldEvent.kind == "npc_shared_rumour",
            WorldEvent.actor_id == gorrik.content_id,
            WorldEvent.target_id == mara.content_id,
        )
    )).scalar_one() == 1

    relationship = (await session.execute(
        select(NPCRelationship).where(
            NPCRelationship.source_npc_content_id == mara.content_id,
            NPCRelationship.target_id == gorrik.content_id,
        )
    )).scalar_one()
    assert relationship.familiarity > 0
    assert relationship.trust > 0

    retrieved = await retrieve_memory_rows(
        session,
        mara.content_id,
        query_tags=frozenset({"rot", "road"}),
        now_minute=20,
        limit=1,
        mark_recalled=True,
    )
    assert retrieved == [received]
    assert received.last_recalled_minute == 20


async def test_goals_remain_private_world_state_not_player_quests(session):
    _hall, _ante, gorrik, _mara = await _prototype_world(session)
    await _clock_at_zero(session)
    content = FakeContent(npc_profiles={
        gorrik.content_id: _profile(
            gorrik.content_id,
            home="test-ante",
            target_kind="self",
        ),
    })

    await LivingWorldService(
        config=_config(), content=content,
    ).advance(session, wall_now=1, active_room_ids=())

    [goal] = (await session.execute(
        select(NPCGoal).where(NPCGoal.npc_content_id == gorrik.content_id)
    )).scalars().all()
    assert goal.goal_key == "private-direction"
    assert goal.context["authored"]["desire"].startswith("Follow a private")
    assert "quest_states" not in NPCGoal.metadata.tables


async def test_reflection_evidence_query_is_bounded_and_semantically_exact(
    session,
):
    _hall, _ante, gorrik, _mara = await _prototype_world(session)
    for index in range(24):
        await remember_once(
            session,
            Memory(
                id=f"observation:{index:02}",
                owner_id=gorrik.content_id,
                kind="observation",
                summary=f"Observation {index}",
                tags=frozenset({"rot", f"thread-{index % 3}"}),
                importance=float(index % 10 + 1),
                confidence=0.75,
                occurred_at=index,
            ),
        )
    await remember_once(
        session,
        Memory(
            id="reflection:old",
            owner_id=gorrik.content_id,
            kind="reflection",
            summary="An older conclusion.",
            tags=frozenset({"reflection", "rot"}),
            importance=10.0,
            confidence=0.9,
            occurred_at=100,
        ),
    )

    all_rows = await memory_rows(session, gorrik.content_id)
    bounded_rows = await reflection_source_rows(session, gorrik.content_id)
    full_result = synthesize_reflection(
        (memory_from_row(row) for row in all_rows),
        owner_id=gorrik.content_id,
        world_minute=2_000,
    )
    bounded_result = synthesize_reflection(
        (memory_from_row(row) for row in bounded_rows),
        owner_id=gorrik.content_id,
        world_minute=2_000,
    )

    assert len(bounded_rows) == 4
    assert all(row.kind != "reflection" for row in bounded_rows)
    assert bounded_result == full_result


async def test_kingdom_goal_resolves_to_its_capital_room(session):
    hall, _ante, gorrik, _mara = await _prototype_world(session)
    await _clock_at_zero(session)
    content = FakeContent(
        kingdoms={
            "far-kingdom": {"capital_location_id": hall.content_id},
        },
        npc_profiles={
            gorrik.content_id: _profile(
                gorrik.content_id,
                home="test-ante",
                windows=(10, 500, 1_000),
                target_kind="kingdom",
                target_id="far-kingdom",
                goal_priority=5,
            ),
        },
    )

    await LivingWorldService(
        config=_config(),
        content=content,
    ).advance(session, wall_now=20 * 60, active_room_ids=())

    goal = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == gorrik.content_id,
            NPCGoal.goal_key == "private-direction",
        )
    )).scalar_one()
    assert goal.context["current_intention"]["target_room_id"] == hall.id


async def test_complete_authored_catalogue_survives_a_dormant_day(session):
    await get_or_seed_default_room(session)
    await _clock_at_zero(session)
    config = _config(catchup_cap_minutes=MINUTES_IN_DAY)

    result = await LivingWorldService(
        config=config,
    ).advance(
        session,
        wall_now=MINUTES_IN_DAY * 60,
        active_room_ids=(),
    )

    living_count = (await session.execute(
        select(func.count()).select_from(NPCRow).where(NPCRow.is_alive.is_(True))
    )).scalar_one()
    goal_owners = set((await session.execute(
        select(NPCGoal.npc_content_id)
    )).scalars())
    assert result.to_minute == MINUTES_IN_DAY
    assert result.deliberations >= living_count * 3
    assert len(goal_owners) == living_count
    assert result.processed_events < _config().max_events_per_advance
    overflow_conversations = (await session.execute(
        select(func.count()).select_from(ScheduledWorldEvent).where(
            ScheduledWorldEvent.kind == "npc_conversation",
            ScheduledWorldEvent.status == "cancelled",
            ScheduledWorldEvent.last_error == "conversation budget exhausted",
        )
    )).scalar_one()
    assert overflow_conversations <= config.max_conversation_turns
