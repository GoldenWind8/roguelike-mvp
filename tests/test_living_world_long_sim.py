from sqlalchemy import func, select

from backend.living_world.service import LivingWorldConfig, LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.models import (
    NPCMemory,
    NPCRow,
    Room,
    TriggerFiring,
    WorldEvent,
    WorldState,
)
from backend.seeds import get_or_seed_default_room


async def test_three_day_autonomous_world_stays_coherent(session):
    await get_or_seed_default_room(session)
    service = LivingWorldService(config=LivingWorldConfig(
        game_minutes_per_real_minute=1440,
        catchup_cap_minutes=1440,
        max_events_per_advance=5000,
    ))
    results = [await service.advance(session, 0, ())]
    trigger_results = [await advance_authored_triggers(
        session,
        from_minute=results[0].from_minute,
        to_minute=results[0].to_minute,
        active_room_ids=(),
    )]
    for day in range(1, 4):
        result = await service.advance(session, day * 60, ())
        results.append(result)
        trigger_results.append(await advance_authored_triggers(
            session,
            from_minute=result.from_minute,
            to_minute=result.to_minute,
            active_room_ids=(),
        ))

    assert results[-1].to_minute == 3 * 1440
    assert sum(result.deliberations for result in results) >= 3 * 22
    assert sum(result.conversations for result in results) > 0
    assert sum(result.memories_created for result in results) > 0
    assert sum(result.fired for result in trigger_results) > 0

    state = await session.get(WorldState, 1)
    assert state.revision >= 4
    assert (await session.execute(
        select(func.count()).select_from(WorldEvent)
    )).scalar_one() > 100
    assert (await session.execute(
        select(func.count()).select_from(NPCMemory)
    )).scalar_one() > 20
    assert (await session.execute(
        select(func.count()).select_from(TriggerFiring)
    )).scalar_one() > 0

    rooms = {
        room.id: room
        for room in (await session.execute(select(Room))).scalars()
    }
    people = (await session.execute(select(NPCRow))).scalars().all()
    assert len({person.content_id for person in people}) == len(people)
    for person in people:
        room = rooms[person.room_id]
        assert 0 <= person.x < room.width
        assert 0 <= person.y < room.height
        assert room.terrain[person.y][person.x] == "."
