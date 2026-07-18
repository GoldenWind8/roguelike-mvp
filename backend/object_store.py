"""Mapper between `object_instances` rows (at rest) and RoomObject live state.

The object twin of npc_store: room_loader maps design data (read-only at
play time), this module maps what play DID to an object — today, chest
lifecycle (opened + leftover contents). Unlike npc_store's save-on-eviction
rhythm, chest state is written through at each mutation edge (an open
resolves, an item is taken): both paths already touch the DB outside round
resolution, and write-through means an evicted or crashed room can never
forget a looted chest.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ObjectInstance


async def apply_object_states(session: AsyncSession, room_id: int, objects) -> None:
    """Overlay saved lifecycle state onto freshly-loaded template objects.
    Objects play never touched have no row and load in their design state."""
    rows = (await session.execute(
        select(ObjectInstance).where(ObjectInstance.room_id == room_id)
    )).scalars().all()
    by_id = {obj.id: obj for obj in objects}
    for row in rows:
        obj = by_id.get(row.object_id)
        if obj is not None:
            obj.opened = row.opened
            obj.contents = list(row.contents or [])


async def save_object_state(session: AsyncSession, room_id: int, obj) -> None:
    """Upsert one object's lifecycle state and commit."""
    row = await session.get(ObjectInstance, (room_id, obj.id))
    if row is None:
        row = ObjectInstance(room_id=room_id, object_id=obj.id)
        session.add(row)
    row.opened = obj.opened
    row.contents = list(obj.contents)
    await session.commit()


async def reset_objects(session: AsyncSession) -> None:
    """DEV: forget everything play did to objects — chests re-arm on the
    next room load. The object half of seeds.reset_npcs."""
    await session.execute(ObjectInstance.__table__.delete())
    await session.commit()
