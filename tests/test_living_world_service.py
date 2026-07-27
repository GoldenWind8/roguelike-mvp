"""Database-backed integration tests for the dormant NPC simulation."""

from dataclasses import dataclass, field

from sqlalchemy import func, select

from backend.living_world.service import (
    LivingWorldConfig,
    LivingWorldService,
)
from backend.living_world.store import epoch_datetime, retrieve_memory_rows
from backend.models import (
    NPCGoal,
    NPCMemory,
    NPCRelationship,
    NPCRow,
    Room,
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


@dataclass
class FakeContent:
    npc_profiles: dict = field(default_factory=dict)
    rumors: dict = field(default_factory=dict)


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


async def test_complete_authored_catalogue_survives_a_dormant_day(session):
    await get_or_seed_default_room(session)
    await _clock_at_zero(session)

    result = await LivingWorldService(
        config=_config(catchup_cap_minutes=MINUTES_IN_DAY),
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
