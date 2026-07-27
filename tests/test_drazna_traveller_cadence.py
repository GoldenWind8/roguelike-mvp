from sqlalchemy import select, update

from backend.living_world.service import LivingWorldConfig, LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.living_world_content import load_living_world_content
from backend.models import (
    NPCRow,
    Room,
    ScheduledWorldEvent,
    TriggerFiring,
    WorldEvent,
)
from backend.seeds import get_or_seed_default_room


def test_edda_prepares_the_erased_route_until_the_authored_departure():
    content = load_living_world_content()
    edda = content.npc_profiles["edda-marr"]
    return_goal = next(
        goal
        for goal in edda["private_goals"]
        if goal["id"] == "return-to-drazna"
    )

    # A permanent kingdom target is an unconditional travel instruction. Edda
    # is stranded, so her ordinary intention is to prove the route while the
    # finite Grey Heron story remains the event that can actually take her.
    assert return_goal["target"] == {
        "kind": "rumor",
        "id": "east-road-is-closed",
    }
    wren = content.npc_profiles["wren-no-house"]
    remembered_water = next(
        goal
        for goal in wren["private_goals"]
        if goal["id"] == "follow-remembered-water"
    )
    assert remembered_water["target"] == {
        "kind": "rumor",
        "id": "rot-speaks-in-dreams",
    }
    assert not edda["offscreen_policy"]["can_relocate"]
    assert not wren["offscreen_policy"]["can_relocate"]
    assert {
        anchor["location_id"]
        for profile in (edda, wren)
        for anchor in profile["schedule"]
    } == {"oakrun_pilgrims_hollow"}

    departure = content.triggers["wren-leaves-for-drazna"]
    assert next(
        condition["npc_ids"]
        for condition in departure["conditions"]
        if condition["kind"] == "co_located"
    ) == ["wren-no-house", "edda-marr"]
    assert any(
        condition["kind"] == "carriage_arrives"
        and condition["carriage_id"] == "grey-heron"
        and condition["location_id"] == "oakrun_pilgrims_hollow"
        for condition in departure["conditions"]
    )
    assert {
        (effect["npc_id"], effect["destination_location_id"])
        for effect in departure["effects"]
        if effect["kind"] == "board_carriage"
    } == {
        ("edda-marr", "drazna_lantern_quays"),
        ("wren-no-house", "drazna_lantern_quays"),
    }
    for visitor_id in (
        "hester-oakrun-carriage",
        "maud-oakrun-orchard",
        "tom-oakrun-stable",
    ):
        visitor = content.npc_profiles[visitor_id]
        assert visitor["schedule"][-1]["location_id"] == (
            "oakrun_pilgrims_hollow"
        )
    maud_meeting = content.triggers["maud-wren-root-memory"]
    assert any(
        condition["kind"] == "npc_at"
        and condition["npc_id"] == "maud-oakrun-orchard"
        and condition["location_id"] == "oakrun_pilgrims_hollow"
        for condition in maud_meeting["conditions"]
    )


async def test_edda_does_not_commute_to_drazna_during_fourteen_local_days(
    session,
):
    await get_or_seed_default_room(session)
    await session.execute(
        update(NPCRow)
        .where(NPCRow.content_id != "edda-marr")
        .values(is_alive=False)
    )
    service = LivingWorldService(config=LivingWorldConfig(
        game_minutes_per_real_minute=1440,
        catchup_cap_minutes=1440,
        max_events_per_advance=5000,
        max_conversations_per_advance=0,
    ))

    results = [
        await service.advance(session, day * 60, ())
        for day in range(15)
    ]

    rooms = {
        room.id: room
        for room in (await session.execute(select(Room))).scalars()
    }
    content = load_living_world_content()

    def kingdom_id(room_id):
        room = rooms.get(room_id)
        location = (
            content.locations.get(room.content_id)
            if room is not None and room.content_id
            else None
        )
        return location["kingdom_id"] if location is not None else None

    journeys = (await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.actor_id == "edda-marr",
            ScheduledWorldEvent.status == "resolved",
            ScheduledWorldEvent.kind.in_((
                "npc_arrive_room",
                "npc_routine_anchor",
            )),
        )
    )).scalars()
    for journey in journeys:
        payload = journey.payload or {}
        path = payload.get("route_room_ids") or [
            payload.get("from_room_id"),
            payload.get("to_room_id"),
        ]
        assert all(
            kingdom_id(first) == kingdom_id(second)
            for first, second in zip(path, path[1:])
            if kingdom_id(first) is not None
            and kingdom_id(second) is not None
        )

    cross_border_arrivals = [
        event
        for event in (await session.execute(
            select(WorldEvent).where(
                WorldEvent.actor_id == "edda-marr",
                WorldEvent.kind == "npc_arrived_room",
            )
        )).scalars()
        if kingdom_id((event.payload or {}).get("from_room_id"))
        != kingdom_id((event.payload or {}).get("to_room_id"))
    ]
    edda = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == "edda-marr")
    )).scalar_one()

    assert not cross_border_arrivals
    assert kingdom_id(edda.room_id) == "amberfall"
    assert sum(result.deliberations for result in results) == 14 * 4
    assert sum(result.movements for result in results) == 0


async def test_grey_heron_departure_is_durable_past_the_next_overnight_anchor(
    session,
):
    await get_or_seed_default_room(session)
    travelling_party = {"edda-marr", "wren-no-house", "jory-rusk"}
    await session.execute(
        update(NPCRow)
        .where(NPCRow.content_id.not_in(travelling_party))
        .values(is_alive=False)
    )
    service = LivingWorldService(config=LivingWorldConfig(
        game_minutes_per_real_minute=1440,
        catchup_cap_minutes=1440,
        max_events_per_advance=5000,
        max_conversations_per_advance=0,
    ))

    for day in range(10):
        result = await service.advance(session, day * 60, ())
        await advance_authored_triggers(
            session,
            from_minute=result.from_minute,
            to_minute=result.to_minute,
            active_room_ids=(),
        )

    firing = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "wren-leaves-for-drazna",
        )
    )).scalar_one()
    rooms = {
        room.id: room.content_id
        for room in (await session.execute(select(Room))).scalars()
    }
    travellers = {
        npc.content_id: rooms[npc.room_id]
        for npc in (await session.execute(
            select(NPCRow).where(NPCRow.content_id.in_((
                "edda-marr",
                "wren-no-house",
            )))
        )).scalars()
    }
    boardings = list((await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "npc_boarded_carriage",
            WorldEvent.actor_id.in_(("edda-marr", "wren-no-house")),
        )
    )).scalars())
    post_departure_returns = [
        event
        for event in (await session.execute(
            select(ScheduledWorldEvent).where(
                ScheduledWorldEvent.actor_id.in_((
                    "edda-marr",
                    "wren-no-house",
                )),
                ScheduledWorldEvent.due_minute > firing.fired_at_minute,
                ScheduledWorldEvent.status == "resolved",
                ScheduledWorldEvent.kind.in_((
                    "npc_arrive_room",
                    "npc_routine_anchor",
                )),
            )
        )).scalars()
        if (event.payload or {}).get("to_room_id") is not None
        and rooms[(event.payload or {})["to_room_id"]].startswith("oakrun_")
    ]

    assert firing.outcome == "applied"
    assert travellers == {
        "edda-marr": "drazna_lantern_quays",
        "wren-no-house": "drazna_lantern_quays",
    }
    assert {event.actor_id for event in boardings} == {
        "edda-marr",
        "wren-no-house",
    }
    assert not post_departure_returns
