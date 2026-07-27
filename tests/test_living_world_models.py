"""Persistence invariants for the autonomous living world.

These tests deliberately exercise durable NPC-owned goals rather than a
player quest log. The simulator is free to arrive later; its authoritative
state already has stable identities, deterministic time, idempotent scheduled
work, provenance-bearing memories, directional relationships, and world truth.
"""
import json

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from backend.db import Base, _backfill_columns
from backend.models import (
    FrontierExit,
    FrontierNode,
    NPCGoal,
    NPCMemory,
    NPCRelationship,
    NPCRow,
    ScheduledWorldEvent,
    TriggerFiring,
    WorldEvent,
    WorldFact,
    WorldState,
)
from backend.npc_store import get_npc_row_by_content_id
from backend.seeds import reset_npcs, seed_default_rooms


def test_living_world_schema_is_additive_and_has_no_quest_tracker():
    tables = set(Base.metadata.tables)
    assert {
        "world_state",
        "world_events",
        "scheduled_world_events",
        "npc_memories",
        "npc_relationships",
        "npc_goals",
        "trigger_firings",
        "world_facts",
        "frontier_nodes",
        "frontier_exits",
    } <= tables
    assert "quest_states" not in tables
    # A memory keys story identity directly and survives replacement of the
    # physical NPC row; it must never depend on the numeric row id.
    assert not NPCMemory.__table__.c.npc_content_id.foreign_keys


async def test_seeded_npcs_have_stable_unique_authored_identities(session):
    await seed_default_rooms(session)
    rows = (await session.execute(
        select(NPCRow).order_by(NPCRow.content_id)
    )).scalars().all()

    assert len(rows) == 2
    assert {row.content_id for row in rows} == {
        row.persona["id"] for row in rows
    }
    assert all(row.content_id for row in rows)

    found = await get_npc_row_by_content_id(session, rows[0].content_id)
    assert found is rows[0]


async def test_story_records_survive_npc_row_reset(session):
    await seed_default_rooms(session)
    gorrik = (await session.execute(
        select(NPCRow).where(NPCRow.name == "Gorrik")
    )).scalar_one()
    old_content_id = gorrik.content_id
    session.add(NPCMemory(
        memory_key=f"promise:{old_content_id}:120",
        npc_content_id=old_content_id,
        kind="promise",
        summary="He promised to keep the north door barred.",
        tags=["door", "promise"],
        source_chain=[old_content_id],
        importance=0.8,
        confidence=1.0,
        world_minute=120,
    ))
    await session.commit()
    # reset_npcs intentionally replaces rows in-place; release this test's
    # loaded instance so SQLAlchemy does not confuse the replacement with it.
    session.expunge_all()

    await reset_npcs(session)

    replacement = await get_npc_row_by_content_id(session, old_content_id)
    memory = (await session.execute(
        select(NPCMemory).where(NPCMemory.npc_content_id == old_content_id)
    )).scalar_one()
    assert replacement is not None
    assert replacement.persona["id"] == old_content_id
    assert memory.summary.startswith("He promised")


async def test_living_world_records_round_trip_with_safe_defaults(session):
    state = WorldState(world_seed=8841)
    event = WorldEvent(
        dedupe_key="departure:basil:480",
        kind="npc_departed",
        world_minute=480,
        actor_id="basil-reed",
        target_id="north-road",
        summary="Basil left before the morning bells.",
        witnesses=["clara-reed"],
        payload={"direction": "north"},
    )
    scheduled = ScheduledWorldEvent(
        dedupe_key="arrive:basil:first-kingdom:600",
        kind="arrive",
        due_minute=600,
        priority=10,
        actor_id="basil-reed",
        target_id="first-kingdom",
    )
    relationship = NPCRelationship(
        source_npc_content_id="clara-reed",
        target_kind="npc",
        target_id="basil-reed",
        affinity=80,
        trust=35,
        fear=20,
        intimacy=75,
        familiarity=100,
        flags=["family", "spouse"],
    )
    reverse_relationship = NPCRelationship(
        source_npc_content_id="basil-reed",
        target_kind="npc",
        target_id="clara-reed",
        affinity=75,
        trust=50,
        fear=5,
        intimacy=70,
        familiarity=100,
        flags=["family", "spouse"],
    )
    goal = NPCGoal(
        npc_content_id="basil-reed",
        goal_key="learn-origin-of-rot",
        kind="investigate",
        target_id="first-kingdom",
        priority=70,
        urgency=40,
        status="active",
        created_at_minute=480,
        # Four reconsiderations per day; locomotion continues between them.
        next_deliberation_minute=840,
        plan_steps=[
            {"action": "travel", "target": "north-road"},
            {"action": "ask", "target": "caravan-master"},
        ],
    )
    fact = WorldFact(
        fact_key="rot:first-recorded:first-kingdom",
        subject_id="the-rot",
        predicate="first_recorded_at",
        object_id="first-kingdom",
        value={"certainty": "disputed"},
        confidence=0.65,
        established_at_minute=30,
        updated_at_minute=480,
    )
    session.add_all([
        state,
        event,
        scheduled,
        relationship,
        reverse_relationship,
        goal,
        fact,
    ])
    await session.flush()
    memory = NPCMemory(
        memory_key="observation:clara:basil-departed:480",
        npc_content_id="clara-reed",
        kind="observation",
        subject_id="basil-reed",
        object_id="north-road",
        summary="She saw Basil take the north road before dawn.",
        tags=["departure", "north-road"],
        source_chain=["clara-reed"],
        source_event_id=event.id,
        importance=0.7,
        confidence=1.0,
        world_minute=480,
    )
    firing = TriggerFiring(
        trigger_id="basil-leaves-after-rumour",
        scope_id="basil-reed",
        actor_npc_content_id="basil-reed",
        dedupe_key="basil-leaves-after-rumour:1",
        ordinal=1,
        fired_at_minute=480,
        event_id=event.id,
        evidence={"fact": fact.fact_key},
    )
    session.add_all([memory, firing])
    await session.commit()

    assert state.id == 1
    assert state.world_minute == 0
    assert state.revision == 0
    assert state.variables == {}
    assert scheduled.status == "pending"
    assert scheduled.attempt_count == 0
    assert scheduled.payload == {}
    assert memory.payload == {}
    assert memory.shareable is True
    assert memory.secrecy == 0.0
    assert memory.cascade_depth == 0
    assert goal.progress == 0.0
    assert goal.current_step == 0
    assert relationship.trust != reverse_relationship.trust
    assert fact.visibility == "hidden"


async def test_scheduled_actions_are_idempotent(session):
    session.add(ScheduledWorldEvent(
        dedupe_key="conversation:basil:clara:300",
        kind="begin_conversation",
        due_minute=300,
        actor_id="basil-reed",
        target_id="clara-reed",
    ))
    await session.commit()

    session.add(ScheduledWorldEvent(
        dedupe_key="conversation:basil:clara:300",
        kind="begin_conversation",
        due_minute=301,
        actor_id="basil-reed",
        target_id="clara-reed",
    ))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_frontier_provenance_and_rising_discovery_pressure_persist(session):
    start = await seed_default_rooms(session)
    node = FrontierNode(
        node_key="frontier:8841:north:1",
        room_id=start.id,
        world_seed=8841,
        generation_seed=921044,
        depth=1,
        biome="wind-fields",
        generator_kind="overworld",
        generator_version="1",
        generator_params={"terrain": "meadow", "paths": 2},
        content={"landmark": "a bell frame with no bell"},
        generation_metadata={"attempts": 1},
        discovered_at_minute=540,
    )
    exit_row = FrontierExit(
        source_room_id=start.id,
        source_x=4,
        source_y=0,
        status="frontier",
        discovery_pressure=0.35,
        attempt_count=3,
        roll_seed=7001,
        biome_hint="wind-fields",
        generator_hint={"room_type": "overworld"},
        created_at_minute=540,
        last_attempt_minute=620,
    )
    session.add_all([node, exit_row])
    await session.commit()
    session.expunge_all()

    reloaded = (await session.execute(
        select(FrontierExit).where(FrontierExit.source_room_id == start.id)
    )).scalar_one()
    reloaded_node = (await session.execute(
        select(FrontierNode).where(FrontierNode.node_key == node.node_key)
    )).scalar_one()

    assert reloaded.discovery_pressure == pytest.approx(0.35)
    assert reloaded.attempt_count == 3
    assert reloaded.target_room_id is None
    assert reloaded_node.world_seed == 8841
    assert reloaded_node.content["landmark"].startswith("a bell frame")
    assert reloaded_node.authored_region_id is None


@pytest.mark.parametrize(
    "record",
    [
        NPCRelationship(
            source_npc_content_id="same-person",
            target_kind="npc",
            target_id="same-person",
        ),
        NPCRelationship(
            source_npc_content_id="a",
            target_kind="npc",
            target_id="b",
            trust=101,
        ),
        NPCMemory(
            memory_key="impossible:telepathy:1",
            npc_content_id="a",
            kind="telepathy",
            summary="Knowledge with no carrier.",
            importance=0.5,
            confidence=1.0,
            world_minute=1,
        ),
        NPCGoal(
            npc_content_id="a",
            goal_key="impossible-progress",
            kind="travel",
            progress=1.1,
            created_at_minute=0,
            next_deliberation_minute=0,
        ),
        WorldState(id=2),
        FrontierNode(
            node_key="impossible:negative-depth",
            room_id=1,
            world_seed=1,
            generation_seed=1,
            depth=-1,
            biome="void",
            generator_kind="overworld",
        ),
        FrontierExit(
            source_room_id=1,
            source_x=0,
            source_y=1,
            status="connected",
            target_room_id=None,
            discovery_pressure=0.0,
            roll_seed=1,
        ),
        FrontierExit(
            source_room_id=1,
            source_x=0,
            source_y=2,
            status="frontier",
            discovery_pressure=-0.01,
            roll_seed=2,
        ),
    ],
)
async def test_living_world_constraints_reject_impossible_state(session, record):
    session.add(record)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


def test_legacy_npc_content_ids_are_backfilled_and_indexed():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE npcs ("
            "id INTEGER PRIMARY KEY, "
            "persona JSON NOT NULL"
            ")"
        ))
        conn.execute(
            text("INSERT INTO npcs (id, persona) VALUES (:id, :persona)"),
            {
                "id": 7,
                "persona": json.dumps({
                    "id": "legacy-courier",
                    "name": "Legacy Courier",
                }),
            },
        )

        _backfill_columns(conn)
        # Migration calls must be harmless on every later boot.
        _backfill_columns(conn)

        row = conn.execute(text(
            "SELECT id, content_id FROM npcs"
        )).mappings().one()
        indexes = inspect(conn).get_indexes("npcs")

    engine.dispose()
    assert row == {"id": 7, "content_id": "legacy-courier"}
    assert any(
        index["unique"] and index["column_names"] == ["content_id"]
        for index in indexes
    )


def test_legacy_duplicate_persona_ids_fail_loudly():
    engine = create_engine("sqlite:///:memory:")
    with pytest.raises(RuntimeError, match="both use 'same-authored-person'"):
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE npcs ("
                "id INTEGER PRIMARY KEY, "
                "persona JSON NOT NULL"
                ")"
            ))
            for row_id in (1, 2):
                conn.execute(
                    text("INSERT INTO npcs (id, persona) VALUES (:id, :persona)"),
                    {
                        "id": row_id,
                        "persona": json.dumps({"id": "same-authored-person"}),
                    },
                )
            _backfill_columns(conn)
    engine.dispose()
