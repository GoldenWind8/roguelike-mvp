from sqlalchemy import func, select

from backend.living_world.player_knowledge import world_sync
from backend.living_world.scheduler import deliberation_minutes
from backend.living_world.service import LivingWorldService
from backend.living_world.trigger_runtime import advance_authored_triggers
from backend.living_world_content import load_living_world_content
from backend.models import (
    NPCGoal,
    NPCRow,
    PlayerRow,
    Room,
    TriggerFiring,
    WorldEvent,
    WorldFact,
)
from backend.seeds import get_or_seed_default_room
from backend.situation_defs import get_situation
from backend.situation_store import record_situation_actor_defeat


_DAY = 1440
_DRAZNA_FACT_KEYS = {
    "drazna.low_lantern_list",
    "drazna.walking_bridge_injury",
    "drazna.omitted_names_testified",
    "drazna.undertide_expedition",
    "drazna.vasko_return",
    "drazna.omitted_tablet",
    "drazna.crown_hearing",
    "drazna.gate_seven_climax",
    "drazna.gate_seven_cadence",
    "drazna.gate_seven_resolution",
    "drazna.walking_ward_after_gate",
    "drazna.luka_last_window",
}


async def _seed_world(session) -> dict[str, Room]:
    await get_or_seed_default_room(session)
    await LivingWorldService().advance(session, 0, ())
    return {
        room.content_id: room
        for room in (await session.execute(select(Room))).scalars()
        if room.content_id
    }


async def _facts(session) -> dict[str, dict]:
    return {
        fact.fact_key: fact.value
        for fact in (await session.execute(
            select(WorldFact).where(WorldFact.fact_key.in_(_DRAZNA_FACT_KEYS))
        )).scalars()
    }


async def _firings(session) -> dict[str, str]:
    return {
        firing.trigger_id: firing.outcome
        for firing in (await session.execute(select(TriggerFiring))).scalars()
    }


async def _npc(session, content_id: str) -> NPCRow:
    return (await session.execute(
        select(NPCRow).where(NPCRow.content_id == content_id)
    )).scalar_one()


async def _advance_story(session, through_day: int, *, from_day: int = 0):
    return await advance_authored_triggers(
        session,
        from_minute=from_day * _DAY,
        to_minute=through_day * _DAY,
        active_room_ids=(),
    )


async def test_successful_drazna_arc_matrix_stays_coherent_for_sixty_days(session):
    rooms = await _seed_world(session)
    await _advance_story(session, 31)

    expected = {
        "drazna.low_lantern_list": {
            "state": "sold",
            "buyer": "floodwarden-office",
            "altered": True,
        },
        "drazna.omitted_names_testified": {
            "state": "deposited",
            "count": 14,
            "public_names": 9,
        },
        "drazna.undertide_expedition": {
            "state": "launched",
            "route": "dry-dock",
            "crew": ["pava", "vasko", "vesna", "luka"],
        },
        "drazna.vasko_return": {
            "state": "returned",
            "ledger": "closure-payroll",
            "survivor_route": "marked",
        },
        "drazna.omitted_tablet": {
            "state": "stolen",
            "motive": "erase-arrest-link",
            "copy_survives": True,
        },
        "drazna.crown_hearing": {
            "state": "opened",
            "crew_count": 14,
            "first_scar_source": "unresolved",
        },
        "drazna.gate_seven_resolution": {
            "state": "pacified",
            "gate": "vented",
            "names_spoken": 14,
        },
        "drazna.walking_ward_after_gate": {
            "state": "stabilized",
            "evacuated_houses": 0,
        },
    }
    facts = await _facts(session)
    assert {key: facts[key] for key in expected} == expected
    assert "drazna.luka_last_window" not in facts

    firings = await _firings(session)
    assert firings["undertide-expedition-launch"] == "applied"
    assert firings["vasko-returns-with-ledger"] == "applied"
    assert firings["nera-tablet-theft"] == "applied"
    assert firings["mara-alin-hearing-outcome"] == "applied"
    assert firings["odran-cadence-pacified"] == "applied"
    gate_room_id = rooms["drazna_gate_seven"].id
    assert {
        (await _npc(session, npc_id)).room_id
        for npc_id in ("odran-third-bell", "rada-velic", "luka-nen")
    } == {gate_room_id}
    assert {
        "gate-seven-unanswered-flood",
        "gate-seven-cadence-expired",
        "odran-falls-gate-held",
        "gate-seven-killed-aftermath",
        "gate-seven-flood-aftermath",
        "luka-dry-dock-last-window",
        "luka-dry-dock-survives-window",
    }.isdisjoint(firings)

    theft_firing = (await session.execute(
        select(TriggerFiring).where(
            TriggerFiring.trigger_id == "nera-tablet-theft"
        )
    )).scalar_one()
    theft_event = await session.get(WorldEvent, theft_firing.event_id)
    assert theft_event is not None
    assert theft_event.visibility == "public_aftermath"

    remote = rooms["oakrun_crossroads"]
    archive = rooms["drazna_tablet_vault"]
    session.add_all((
        PlayerRow(
            id="arc-remote-reader",
            username="arc-remote-reader",
            password_hash="unused",
            room_id=remote.id,
            x=remote.spawn_points[0][0],
            y=remote.spawn_points[0][1],
            hp=100,
        ),
        PlayerRow(
            id="arc-local-reader",
            username="arc-local-reader",
            password_hash="unused",
            room_id=archive.id,
            x=archive.spawn_points[0][0],
            y=archive.spawn_points[0][1],
            hp=100,
        ),
    ))
    await session.commit()
    remote_sync = await world_sync(
        session,
        player_id="arc-remote-reader",
        current_room_id=remote.id,
    )
    local_sync = await world_sync(
        session,
        player_id="arc-local-reader",
        current_room_id=archive.id,
    )
    theft_event_id = f"event:{theft_event.id}"
    assert theft_event_id not in {
        entry["id"] for entry in remote_sync["chronicle"]
    }
    local_entry = next(
        entry
        for entry in local_sync["chronicle"]
        if entry["id"] == theft_event_id
    )
    assert local_entry["provenance"] == "found"

    before = await _facts(session)
    one_shot_count = (await session.execute(
        select(func.count(TriggerFiring.id)).where(
            TriggerFiring.trigger_id.in_({
                "undertide-expedition-launch",
                "vasko-returns-with-ledger",
                "nera-tablet-theft",
                "mara-alin-hearing-outcome",
                "odran-cadence-pacified",
            })
        )
    )).scalar_one()
    await _advance_story(session, 61, from_day=31)
    assert await _facts(session) == before
    assert (await session.execute(
        select(func.count(TriggerFiring.id)).where(
            TriggerFiring.trigger_id.in_({
                "undertide-expedition-launch",
                "vasko-returns-with-ledger",
                "nera-tablet-theft",
                "mara-alin-hearing-outcome",
                "odran-cadence-pacified",
            })
        )
    )).scalar_one() == one_shot_count


async def test_gate_pacification_cannot_be_performed_by_dead_rada(session):
    await _seed_world(session)
    await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=8 * _DAY - 1,
        active_room_ids=(),
    )
    facts = await _facts(session)
    assert facts["drazna.gate_seven_climax"] == {
        "state": "engaged",
        "gate": "locked",
        "cadence_known": True,
    }
    assert "drazna.gate_seven_resolution" not in facts

    rada = await _npc(session, "rada-velic")
    rada.hp = 0
    rada.is_alive = False
    await session.commit()
    await advance_authored_triggers(
        session,
        from_minute=8 * _DAY - 1,
        to_minute=9 * _DAY,
        active_room_ids=(),
    )

    assert "drazna.gate_seven_resolution" not in await _facts(session)
    assert "odran-cadence-pacified" not in await _firings(session)


async def test_gate_pacification_defers_visible_relocation_then_gathers_witnesses(
    session,
):
    rooms = await _seed_world(session)
    await advance_authored_triggers(
        session,
        from_minute=0,
        to_minute=8 * _DAY - 1,
        active_room_ids=(),
    )
    rada = await _npc(session, "rada-velic")
    luka = await _npc(session, "luka-nen")
    gate = rooms["drazna_gate_seven"]
    assert rada.room_id != gate.id
    assert luka.room_id != gate.id

    await advance_authored_triggers(
        session,
        from_minute=8 * _DAY - 1,
        to_minute=9 * _DAY,
        active_room_ids=(rada.room_id,),
    )
    assert "drazna.gate_seven_resolution" not in await _facts(session)
    assert (await _npc(session, "rada-velic")).room_id == rada.room_id

    await advance_authored_triggers(
        session,
        from_minute=8 * _DAY - 1,
        to_minute=9 * _DAY,
        active_room_ids=(),
    )
    assert (await _facts(session))["drazna.gate_seven_resolution"] == {
        "state": "pacified",
        "gate": "vented",
        "names_spoken": 14,
    }
    assert {
        (await _npc(session, npc_id)).room_id
        for npc_id in ("odran-third-bell", "rada-velic", "luka-nen")
    } == {gate.id}


async def test_withheld_list_secured_tablet_and_suppressed_hearing_are_exclusive(
    session,
):
    rooms = await _seed_world(session)
    rada = await _npc(session, "rada-velic")
    rada.hp = 0
    rada.is_alive = False
    await session.commit()

    await _advance_story(session, 31)
    facts = await _facts(session)
    assert facts["drazna.low_lantern_list"] == {
        "state": "withheld",
        "buyer": "none",
        "altered": False,
    }
    assert facts["drazna.omitted_tablet"] == {
        "state": "secured",
        "motive": "public-reading",
        "copy_survives": True,
    }
    assert facts["drazna.omitted_names_testified"] == {
        "state": "missed",
        "count": 9,
        "public_names": 9,
    }
    assert facts["drazna.crown_hearing"] == {
        "state": "suppressed",
        "crew_count": 9,
        "first_scar_source": "officially-uncertain",
    }

    firings = await _firings(session)
    assert firings["teo-sells-low-lantern-list"] == "missed"
    assert firings["nera-tablet-theft"] == "missed"
    assert firings["mara-alin-hearing-outcome"] == "missed"
    assert "drazna.gate_seven_resolution" not in facts

    alin = await _npc(session, "alin-vey")
    assert (alin.hp, alin.is_alive) == (24, True)
    hearing_goal = (await session.execute(
        select(NPCGoal).where(
            NPCGoal.npc_content_id == "alin-vey",
            NPCGoal.goal_key == "publish-flood-record",
        )
    )).scalar_one()
    assert hearing_goal.status == "blocked"

    rubbing = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.kind == "evidence_left",
            WorldEvent.summary == (
                "A paper rubbing preserves the omitted names in reverse, "
                "hidden behind a public flood map."
            ),
        )
    )).scalar_one()
    assert rubbing.room_id == rooms["drazna_house_of_names"].id
    assert rubbing.visibility == "discoverable"

    before = await _facts(session)
    session.expire_all()
    assert (await _npc(session, "alin-vey")).hp == 24
    await _advance_story(session, 61, from_day=31)
    assert await _facts(session) == before


async def test_missed_expedition_persists_fatal_and_collapsed_aftermath_privately(
    session,
):
    rooms = await _seed_world(session)
    undertide = rooms["drazna_undertide"]
    dry_dock = rooms["drazna_dry_dock"]
    remote = rooms["oakrun_crossroads"]
    observer = PlayerRow(
        id="arc-luka-observer",
        username="arc-luka-observer",
        password_hash="unused",
        room_id=undertide.id,
        x=undertide.spawn_points[0][0],
        y=undertide.spawn_points[0][1],
        hp=100,
    )
    session.add(observer)
    await session.commit()
    initial = await world_sync(
        session,
        player_id=observer.id,
        current_room_id=undertide.id,
    )
    assert next(
        person
        for person in initial["known_people"]
        if person["world_id"] == "luka-nen"
    )["condition"]["kind"] == "well"

    observer.room_id = remote.id
    observer.x, observer.y = remote.spawn_points[0]
    pava = await _npc(session, "pava-mirek")
    pava.hp = 0
    pava.is_alive = False
    await session.commit()

    await _advance_story(session, 31)
    facts = await _facts(session)
    assert facts["drazna.undertide_expedition"] == {
        "state": "missed",
        "route": "dry-dock",
        "crew": ["luka"],
    }
    assert facts["drazna.vasko_return"] == {
        "state": "injured",
        "ledger": "lost",
        "survivor_route": "collapsed",
    }
    assert facts["drazna.luka_last_window"] == {
        "state": "dead",
        "location": "dry-dock",
        "testimony_deposited": False,
    }
    assert facts["drazna.gate_seven_climax"] == {
        "state": "flooded",
        "gate": "jammed",
        "cadence_known": True,
    }
    assert facts["drazna.walking_ward_after_gate"] == {
        "state": "partly-collapsed",
        "evacuated_houses": 3,
    }
    assert facts["drazna.gate_seven_resolution"] == {
        "state": "flooded",
        "gate": "jammed",
        "names_spoken": 9,
    }
    assert "drazna.gate_seven_cadence" not in facts

    luka = await _npc(session, "luka-nen")
    vasko = await _npc(session, "vasko-mirek")
    sima = await _npc(session, "sima-dren")
    rada = await _npc(session, "rada-velic")
    assert (luka.hp, luka.is_alive, luka.room_id) == (0, False, dry_dock.id)
    assert (vasko.hp, vasko.is_alive) == (10, True)
    assert (sima.hp, sima.is_alive) == (8, True)
    assert (rada.hp, rada.is_alive) == (22, True)
    fate = (await session.execute(
        select(WorldFact).where(
            WorldFact.fact_key == "npc-fate:luka-nen"
        )
    )).scalar_one()
    assert fate.value["is_alive"] is False

    firings = await _firings(session)
    assert firings["undertide-expedition-launch"] == "missed"
    assert firings["vasko-returns-with-ledger"] == "missed"
    assert firings["luka-dry-dock-last-window"] == "applied"
    assert firings["gate-seven-unanswered-flood"] == "applied"
    assert firings["gate-seven-flood-aftermath"] == "applied"
    assert {
        "gate-seven-climax",
        "gate-seven-cadence-expired",
        "odran-cadence-pacified",
        "gate-seven-pacified-aftermath",
        "gate-seven-killed-aftermath",
        "luka-dry-dock-survives-window",
    }.isdisjoint(firings)

    remote_sync = await world_sync(
        session,
        player_id=observer.id,
        current_room_id=remote.id,
    )
    remote_luka = next(
        person
        for person in remote_sync["known_people"]
        if person["world_id"] == "luka-nen"
    )
    assert remote_luka["availability"] == "unknown"
    assert remote_luka["condition"]["kind"] == "well"
    death_trace = (
        "A lone diver died in the dry dock after returning for the "
        "unanswered knocks."
    )
    assert not any(
        death_trace == entry["body"]
        for entry in remote_sync["chronicle"]
    )

    observer.room_id = dry_dock.id
    observer.x, observer.y = dry_dock.spawn_points[0]
    await session.commit()
    found_sync = await world_sync(
        session,
        player_id=observer.id,
        current_room_id=dry_dock.id,
    )
    found_luka = next(
        person
        for person in found_sync["known_people"]
        if person["world_id"] == "luka-nen"
    )
    assert found_luka["availability"] == "dead"
    assert found_luka["condition"]["kind"] == "dead"
    found_death = next(
        entry
        for entry in found_sync["chronicle"]
        if entry["body"] == death_trace
    )
    assert found_death["provenance"] == "found"
    assert found_death["actor_world_ids"] == []

    session.expire_all()
    persisted_luka = await _npc(session, "luka-nen")
    assert (persisted_luka.hp, persisted_luka.is_alive) == (0, False)


async def test_healed_luka_survives_only_the_missed_expedition_branch(session):
    await _seed_world(session)
    pava = await _npc(session, "pava-mirek")
    pava.hp = 0
    pava.is_alive = False
    luka = await _npc(session, "luka-nen")
    luka.max_hp = 30
    luka.hp = 30
    await session.commit()

    await _advance_story(session, 31)
    facts = await _facts(session)
    assert facts["drazna.undertide_expedition"]["state"] == "missed"
    assert facts["drazna.luka_last_window"] == {
        "state": "survived-injured",
        "location": "dry-dock",
        "testimony_deposited": False,
    }
    luka = await _npc(session, "luka-nen")
    assert (luka.hp, luka.is_alive) == (18, True)
    firings = await _firings(session)
    assert firings["luka-dry-dock-survives-window"] == "applied"
    assert "luka-dry-dock-last-window" not in firings

    # Later combat damage cannot reopen the already-decided fatal window.
    luka.hp = 1
    await session.commit()
    before = facts["drazna.luka_last_window"]
    await _advance_story(session, 61, from_day=31)
    assert (await _facts(session))["drazna.luka_last_window"] == before
    assert (await _npc(session, "luka-nen")).is_alive is True
    assert "luka-dry-dock-last-window" not in await _firings(session)


async def test_killed_gate_outcome_cannot_be_replaced_over_sixty_days(session):
    rooms = await _seed_world(session)
    definition = get_situation("drazna-gate-seven-reckoning")
    assert definition is not None
    odran = await _npc(session, "odran-third-bell")
    odran.hp = 0
    odran.is_alive = False
    result = await record_situation_actor_defeat(
        session,
        actor_id=odran.content_id,
        room_id=rooms["drazna_gate_seven"].id,
        world_minute=100,
        witnesses=(),
    )
    assert result is not None and result.outcome == "odran-killed"
    await session.commit()

    await _advance_story(session, 31)
    expected = {
        "state": "odran-killed",
        "gate": "held-by-chain",
        "names_spoken": 0,
    }
    assert (await _facts(session))["drazna.gate_seven_resolution"] == expected
    firings = await _firings(session)
    assert firings["gate-seven-killed-aftermath"] == "applied"
    assert {
        "gate-seven-climax",
        "gate-seven-unanswered-flood",
        "odran-cadence-pacified",
        "gate-seven-pacified-aftermath",
        "gate-seven-cadence-expired",
        "gate-seven-contained-aftermath",
        "gate-seven-flood-aftermath",
    }.isdisjoint(firings)

    await _advance_story(session, 61, from_day=31)
    assert (await _facts(session))["drazna.gate_seven_resolution"] == expected
    events = (await session.execute(
        select(WorldEvent).where(
            WorldEvent.dedupe_key
            == "situation:drazna-gate-seven-reckoning"
        )
    )).scalars().all()
    assert len(events) == 1
    assert events[0].payload["outcome"] == "odran-killed"


def test_every_drazna_npc_has_three_to_six_sparse_deliberations_for_31_days():
    content = load_living_world_content()
    drazna_npcs = {
        profile_id
        for profile_id, profile in content.npc_profiles.items()
        if profile["home_location_id"].startswith("drazna_")
    }
    assert len(drazna_npcs) == 15

    for npc_id in drazna_npcs:
        previous = -1
        for day in range(31):
            minutes = deliberation_minutes(npc_id, day)
            assert 3 <= len(minutes) <= 6
            assert len(minutes) == len(set(minutes))
            assert all(day * _DAY + 360 <= minute <= day * _DAY + 1320 for minute in minutes)
            assert minutes[0] > previous
            previous = minutes[-1]
