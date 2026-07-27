import json

import pytest
from sqlalchemy import select

from backend.living_world.player_knowledge import (
    record_object_discovery,
    world_sync,
)
from backend.living_world.service import LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.models import NPCRow, PlayerRow, Room, WorldState
from backend.object_defs import get_object_definition
from backend.seeds import get_or_seed_default_room
from backend.situation_defs import get_situation
from backend.situation_store import (
    record_situation_actor_defeat,
    resolve_situation_choice,
)


_DAY = 1440
_TRACKER_KEYS = {
    "fact_key",
    "goal_id",
    "objective",
    "objectives",
    "quest",
    "quest_id",
    "quest_state",
    "trigger_id",
}


async def _seed_world(session) -> dict[str, Room]:
    await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())
    return {
        room.content_id: room
        for room in (await session.execute(select(Room))).scalars()
        if room.content_id
    }


async def _player(session, player_id: str, room: Room) -> PlayerRow:
    player = PlayerRow(
        id=player_id,
        username=player_id,
        password_hash="unused",
        room_id=room.id,
        x=room.spawn_points[0][0],
        y=room.spawn_points[0][1],
        hp=100,
    )
    session.add(player)
    await session.commit()
    return player


async def _visit(session, player: PlayerRow, room: Room) -> dict:
    player.room_id = room.id
    player.x, player.y = room.spawn_points[0]
    await session.flush()
    return await world_sync(
        session,
        player_id=player.id,
        current_room_id=room.id,
    )


async def _meet(session, player: PlayerRow, npc_id: str) -> dict:
    npc = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == npc_id)
    )).scalar_one()
    room = await session.get(Room, npc.room_id)
    assert room is not None
    return await _visit(session, player, room)


async def _advance_to_day(session, day: int) -> None:
    await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=day * _DAY,
        active_room_ids=(),
    )
    world = await session.get(WorldState, 1)
    assert world is not None
    world.world_minute = day * _DAY
    await session.commit()


async def _discover(
    session,
    *,
    player: PlayerRow,
    room: Room,
    object_type: str,
    minute: int,
) -> None:
    definition = get_object_definition(object_type)
    assert definition is not None and definition.discovery is not None
    await record_object_discovery(
        session,
        player_id=player.id,
        room_id=room.id,
        object_id=f"audit:{player.id}:{object_type}",
        discovery=definition.discovery,
        world_minute=minute,
    )


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def _assert_no_tracker_leak(payload: dict) -> None:
    assert _TRACKER_KEYS.isdisjoint(
        str(key).lower() for key in _all_keys(payload)
    )
    rendered = json.dumps(payload).lower()
    assert "authored story turn" not in rendered
    assert "an opportunity passed" not in rendered
    assert "loyalties changed" not in rendered


def _entry(payload: dict, body: str) -> dict:
    return next(
        entry
        for entry in payload["chronicle"]
        if entry["body"] == body
    )


async def test_partial_attendance_hides_successful_arcs_until_local_discovery(
    session,
):
    rooms = await _seed_world(session)
    player = await _player(
        session,
        "drazna-partial-success",
        rooms["oakrun_crossroads"],
    )
    known_ids = {
        "teo-latch",
        "nera-bell",
        "vasko-mirek",
        "luka-nen",
        "mara-vey",
        "alin-vey",
        "odran-third-bell",
    }
    for npc_id in sorted(known_ids):
        await _meet(session, player, npc_id)
    await _visit(session, player, rooms["oakrun_crossroads"])

    await _advance_to_day(session, 12)
    remote = await _visit(session, player, rooms["oakrun_crossroads"])
    remote_people = {
        person["world_id"]: person
        for person in remote["known_people"]
        if person["world_id"] in known_ids
    }
    assert set(remote_people) == known_ids
    assert all(
        person["condition"]["kind"] == "well"
        and person["activity"] is None
        and person["dialogue_topics"] == []
        for person in remote_people.values()
    )

    traces = (
        (
            "drazna_reed_market",
            "A Crown receipt pays for four passenger names; the arrest copy returned with seven circled.",
            ),
            (
                "drazna_tablet_vault",
                "A comparison tablet is gone from its salt outline; a paper rubbing remains behind the public flood map.",
            ),
        (
            "drazna_house_of_names",
            "Freshly cut numerals count to fourteen beneath a public tablet that still stops at nine.",
        ),
        (
            "drazna_undertide",
            "Four colored wrist strips and a newly tensioned salvage rope mark a descent toward the dry dock.",
        ),
        (
            "drazna_mud_crown",
            "A water-warped closure ledger has reached Mud Crown, its payroll scored to fourteen.",
        ),
        (
            "drazna_palace_still_water",
            "Fourteen witness stools face a Crown table still set with only nine name cards.",
        ),
        (
            "drazna_gate_seven",
            "The pressure needle has fallen below red while fourteen answered names remain cut in the gate wax.",
        ),
    )
    remote_bodies = {entry["body"] for entry in remote["chronicle"]}
    assert remote_bodies.isdisjoint(body for _room_id, body in traces)
    assert not any(
        "You sold the dive as salvage" in entry["body"]
        for entry in remote["chronicle"]
    )

    payload = remote
    found = 0
    for room_id, body in traces:
        payload = await _visit(session, player, rooms[room_id])
        entry = _entry(payload, body)
        assert entry["provenance"] == "found"
        assert entry["actor_world_ids"] == []
        assert not any(
            name in body
            for name in (
                "Luka",
                "Vasko",
                "Alin",
                "Teo",
                "Nera",
                "Sima",
                "Rada",
                "Odran",
            )
        )
        assert entry["title"] in {"A local trace", "Something left behind"}
        found += 1

    assert found == 7
    payload = await _visit(session, player, rooms["drazna_lantern_quays"])
    departures = [
        entry
        for entry in payload["chronicle"]
        if entry["title"] == "Someone moved on"
    ]
    # Teo, Vasko, and Olek already left anonymous traces.  Luka's deliberate
    # gathering at Gate Seven adds one more without revealing who moved.
    assert len(departures) == 4
    for departure in departures:
        assert departure["body"] == (
            "Signs show that someone left this place between visits."
        )
        assert departure["title"] == "Someone moved on"
        assert departure["provenance"] == "found"
        assert departure["actor_world_ids"] == []
    assert not any(
        "You sold the dive as salvage" in entry["body"]
        for entry in payload["chronicle"]
    )
    private_reasons = (
        "Luka drags Teo below the public waterline",
        "closure payroll wrapped beneath his shirt",
        "make his first offer for the ledger",
    )
    assert not any(
        private in entry["body"]
        for private in private_reasons
        for entry in payload["chronicle"]
    )
    _assert_no_tracker_leak(payload)


async def test_late_arrival_reads_missed_branches_without_offscreen_people_leaks(
    session,
):
    rooms = await _seed_world(session)
    player = await _player(
        session,
        "drazna-partial-missed",
        rooms["oakrun_crossroads"],
    )
    known_ids = {
        "teo-latch",
        "nera-bell",
        "vasko-mirek",
        "luka-nen",
        "mara-vey",
        "alin-vey",
    }
    for npc_id in sorted(known_ids):
        await _meet(session, player, npc_id)
    await _visit(session, player, rooms["oakrun_crossroads"])

    for npc_id in ("pava-mirek", "rada-velic"):
        npc = (await session.execute(
            select(NPCRow).where(NPCRow.content_id == npc_id)
        )).scalar_one()
        npc.hp = 0
        npc.is_alive = False
    await session.commit()
    await _advance_to_day(session, 12)

    remote = await _visit(session, player, rooms["oakrun_crossroads"])
    remote_people = {
        person["world_id"]: person
        for person in remote["known_people"]
        if person["world_id"] in known_ids
    }
    assert set(remote_people) == known_ids
    assert all(
        person["condition"]["kind"] == "well"
        and person["activity"] is None
        for person in remote_people.values()
    )
    concealed = (
        "Luka died in the dry dock after returning alone for the unanswered knocks.",
        "Vasko surfaced alone after losing the payroll satchel and two fingers to the closing chain.",
        "Palace guards broke Alin's writing hand while clearing the unlicensed hearing.",
    )
    assert {
        entry["body"] for entry in remote["chronicle"]
    }.isdisjoint(concealed)

    traces = (
        (
            "drazna_dry_dock",
            "A lone diver died in the dry dock after returning for the unanswered knocks.",
        ),
        (
            "drazna_undertide",
            "A lone diver surfaced after losing the payroll satchel and two fingers to the closing chain.",
        ),
        (
            "drazna_palace_still_water",
            "Palace guards broke a scribe's writing hand while clearing the unlicensed hearing.",
        ),
        (
            "drazna_reed_market",
            "An unsigned crown receipt offers payment for four names; the broker's side of the paper remains blank.",
        ),
        (
            "drazna_low_lantern_den",
            "An unsold passenger list lies under a false manifest, its four selected names never circled.",
        ),
        (
            "drazna_tablet_vault",
            "The comparison tablet rests inside a fresh salt ring; a Reed Market wrapping fiber is caught outside the unbroken seal.",
        ),
        (
            "drazna_house_of_names",
            "The supplemental tablet ends at nine; five ruled spaces beneath it remain uncut.",
        ),
        (
            "drazna_crown_sluice",
            "A junior pressure key remains on the floodwarden board; blue roof-thread is tied around the untouched hook.",
        ),
        (
            "drazna_undertide",
            "A copied passenger list lies beside a survivor bunk without the expected wet market-shoe prints.",
        ),
    )
    payload = remote
    for room_id, body in traces:
        payload = await _visit(session, player, rooms[room_id])
        entry = _entry(payload, body)
        assert entry["provenance"] == "found"
        assert entry["actor_world_ids"] == []

    contradictory = (
        "A Crown receipt pays for four passenger names",
        "A comparison tablet is gone from its salt outline",
        "Freshly cut numerals count to fourteen",
        "A junior pressure key is missing",
        "Wet footprints from a market shoe",
    )
    rendered_bodies = "\n".join(
        entry["body"] for entry in payload["chronicle"]
    )
    assert not any(text in rendered_bodies for text in contradictory)
    revealed_people = {
        person["world_id"]: person
        for person in payload["known_people"]
        if person["world_id"] in {"luka-nen", "vasko-mirek", "alin-vey"}
    }
    assert revealed_people["luka-nen"]["condition"]["kind"] == "dead"
    assert revealed_people["vasko-mirek"]["condition"]["kind"] == "wounded"
    assert revealed_people["alin-vey"]["condition"]["kind"] == "wounded"
    luka_departure = _entry(
        payload,
        "Signs show that someone left this place between visits.",
    )
    assert luka_departure["provenance"] == "found"
    assert luka_departure["actor_world_ids"] == []
    assert not any(
        "Luka follows the closing dry line alone" in entry["body"]
        for entry in payload["chronicle"]
    )
    assert len(traces) == 9
    _assert_no_tracker_leak(payload)


@pytest.mark.parametrize(
    (
        "outcome",
        "choice_id",
        "clue_types",
        "witnessed_body",
        "aftermath_body",
        "condition",
    ),
    [
        (
            "pacified",
            "answer-the-fourteenth",
            (
                "drazna_sluice_tools",
                "drazna_listening_pipe",
                "drazna_omitted_tablets",
            ),
            "Fourteen names were answered at Gate Seven. Odran Third-Bell released the emergency pawl without opening the lower gate.",
            "The pressure needle has fallen below red while fourteen answered names remain cut in the gate wax.",
            "well",
        ),
        (
            "contained",
            "brace-the-counterpressure",
            (
                "drazna_pressure_gauge",
                "drazna_crown_flood_order",
            ),
            "Gate Seven was rebraced to the Walking Ward's counterpressure scale. Odran remains bound, and the deepest passage stays sealed.",
            "A roofwright counterbrace now carries Gate Seven's load; the memorial wax remains unanswered.",
            "well",
        ),
        (
            "odran-killed",
            None,
            (),
            "Odran Third-Bell was slain at Gate Seven. The lower chain stopped between its ninth and tenth answers.",
            "The chain remains fixed around a body, with the pressure needle still above safe.",
            "dead",
        ),
    ],
)
async def test_gate_outcomes_are_witnessed_privately_then_leave_distinct_traces(
    session,
    outcome,
    choice_id,
    clue_types,
    witnessed_body,
    aftermath_body,
    condition,
):
    rooms = await _seed_world(session)
    gate = rooms["drazna_gate_seven"]
    resolver = await _player(session, f"gate-resolver-{outcome}", gate)
    observer = await _player(
        session,
        f"gate-observer-{outcome}",
        rooms["oakrun_crossroads"],
    )
    await _visit(session, resolver, gate)
    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None

    for offset, object_type in enumerate(clue_types):
        await _discover(
            session,
            player=resolver,
            room=gate,
            object_type=object_type,
            minute=100 + offset,
        )
    if choice_id is None:
        odran = (await session.execute(
            select(NPCRow).where(
                NPCRow.content_id == "odran-third-bell"
            )
        )).scalar_one()
        odran.hp = 0
        odran.is_alive = False
        result = await record_situation_actor_defeat(
            session,
            actor_id=odran.content_id,
            room_id=gate.id,
            world_minute=110,
            witnesses=(resolver.id,),
        )
    else:
        result = await resolve_situation_choice(
            session,
            definition=definition,
            choice_id=choice_id,
            player_id=resolver.id,
            room_id=gate.id,
            world_minute=110,
            witnesses=(resolver.id,),
        )
    assert result is not None and result.outcome == outcome
    await session.commit()

    witnessed = await _visit(session, resolver, gate)
    witnessed_entry = _entry(witnessed, witnessed_body)
    assert witnessed_entry["provenance"] == "witnessed"
    assert witnessed_entry["title"] == "A decisive moment"
    if choice_id is not None:
        assert resolver.id in witnessed_entry["actor_world_ids"]
    assert "odran-third-bell" in witnessed_entry["actor_world_ids"]

    remote = await _visit(session, observer, rooms["oakrun_crossroads"])
    assert witnessed_body not in {
        entry["body"] for entry in remote["chronicle"]
    }
    await advance_authored_triggers(
        session,
        from_minute=110,
        to_minute=111,
        active_room_ids=(),
    )
    world = await session.get(WorldState, 1)
    assert world is not None
    world.world_minute = 111
    await session.commit()

    still_remote = await _visit(
        session,
        observer,
        rooms["oakrun_crossroads"],
    )
    assert aftermath_body not in {
        entry["body"] for entry in still_remote["chronicle"]
    }
    local = await _visit(session, observer, gate)
    aftermath_entry = _entry(local, aftermath_body)
    assert aftermath_entry["provenance"] == "found"
    assert aftermath_entry["title"] == "A local trace"
    assert aftermath_entry["actor_world_ids"] == []
    assert witnessed_body not in {
        entry["body"] for entry in local["chronicle"]
    }

    odran_view = next(
        person
        for person in local["known_people"]
        if person["world_id"] == "odran-third-bell"
    )
    assert odran_view["condition"]["kind"] == condition
    other_outcomes = {
        "The pressure needle has fallen below red while fourteen answered names remain cut in the gate wax.",
        "A roofwright counterbrace now carries Gate Seven's load; the memorial wax remains unanswered.",
        "The chain remains fixed around a body, with the pressure needle still above safe.",
    } - {aftermath_body}
    assert {
        entry["body"] for entry in local["chronicle"]
    }.isdisjoint(other_outcomes)
    _assert_no_tracker_leak(local)
