"""Chest lifecycle persistence (docs/LOOT.md): opened state and leftover
contents write through to `object_instances` and survive a room reload —
an opened chest can never be re-armed by room-cycling.
"""
from backend.object_store import reset_objects, save_object_state
from backend.room_loader import load_room
from backend.seeds import seed_default_rooms


def _item(item_id: int, name: str) -> dict:
    return {"id": item_id, "name": name, "description": "d", "rarity": "common",
            "type": "consumable", "art": {"kind": "emoji", "value": "❓"},
            "payload": {"effects": [{"kind": "restore_hp", "amount": 1}]},
            "origin": "seed"}


async def test_opened_chest_survives_room_reload(session):
    room = await seed_default_rooms(session)
    template = await load_room(session, room.id)
    chest = template.objects[0]
    assert chest.opened is False

    # First open: the roll landed two finds nobody carried off.
    chest.opened = True
    chest.contents = [_item(1, "Bread"), _item(2, "Bomb")]
    await save_object_state(session, room.id, chest)

    # The room was evicted; a fresh load overlays the saved state.
    reloaded = await load_room(session, room.id)
    fresh = reloaded.objects[0]
    assert fresh.opened is True
    assert [i["name"] for i in fresh.contents] == ["Bread", "Bomb"]
    assert fresh.to_summary_dict()["contents_count"] == 2

    # Objects play never touched still load in their design state.
    assert all(not obj.opened for obj in reloaded.objects[1:])


async def test_take_updates_the_same_row(session):
    room = await seed_default_rooms(session)
    template = await load_room(session, room.id)
    chest = template.objects[0]
    chest.opened = True
    chest.contents = [_item(1, "Bread"), _item(2, "Bomb")]
    await save_object_state(session, room.id, chest)

    # Someone takes the bread: the upsert overwrites, never duplicates.
    chest.contents.pop(0)
    await save_object_state(session, room.id, chest)

    reloaded = await load_room(session, room.id)
    fresh = reloaded.objects[0]
    assert fresh.opened is True
    assert [i["name"] for i in fresh.contents] == ["Bomb"]


async def test_emptied_chest_stays_opened_and_empty(session):
    room = await seed_default_rooms(session)
    template = await load_room(session, room.id)
    chest = template.objects[0]
    chest.opened = True
    chest.contents = []
    await save_object_state(session, room.id, chest)

    reloaded = await load_room(session, room.id)
    assert reloaded.objects[0].opened is True
    assert reloaded.objects[0].contents == []


async def test_state_for_unknown_object_id_is_ignored(session):
    # A row whose object no longer exists in the design list (edited room)
    # must not break the load or leak onto another object.
    room = await seed_default_rooms(session)
    template = await load_room(session, room.id)
    ghost = type(template.objects[0])(
        id="object_99", type="chest", position=(0, 0),
        label="Chest", description="gone", opened=True,
        contents=[_item(1, "Bread")],
    )
    await save_object_state(session, room.id, ghost)

    reloaded = await load_room(session, room.id)
    assert all(not obj.opened for obj in reloaded.objects)


async def test_reset_objects_rearms_chests(session):
    room = await seed_default_rooms(session)
    template = await load_room(session, room.id)
    chest = template.objects[0]
    chest.opened = True
    chest.contents = [_item(1, "Bread")]
    await save_object_state(session, room.id, chest)

    await reset_objects(session)

    reloaded = await load_room(session, room.id)
    assert reloaded.objects[0].opened is False
    assert reloaded.objects[0].contents == []
