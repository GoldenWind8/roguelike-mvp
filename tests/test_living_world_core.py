from backend.living_world.clock import (
    MINUTES_PER_DAY,
    compute_clock_advance,
    day_phase,
)
from backend.living_world.memory import (
    Memory,
    memory_score,
    retrieve_memories,
    select_conversation_memories,
    synthesize_reflection,
    transmit_rumour,
)
from backend.living_world.movement import (
    RouteEdge,
    next_travel_event,
    shortest_route,
)
from backend.living_world.planner import (
    GoalCandidate,
    IntentionKind,
    choose_intention,
    need_candidate,
)
from backend.living_world.relationships import (
    Relationship,
    apply_relationship_delta,
    bond_word,
)
from backend.living_world.scheduler import (
    deliberation_count,
    deliberation_minutes,
    due_deliberations,
    next_deliberation,
)
from backend.living_world.triggers import (
    TriggerContext,
    evaluate_condition,
    validate_effect,
)


def test_clock_advance_is_bounded_and_records_coalesced_time():
    advance = compute_clock_advance(
        current_minute=120,
        last_wall_at=1_000,
        wall_now=1_000 + 60 * 24,
        game_minutes_per_real_minute=5,
        catchup_cap_minutes=60,
    )
    assert advance.simulated_minutes == 60
    assert advance.to_minute == 180
    assert advance.coalesced_minutes == 60


def test_clock_never_runs_backwards_and_validates_configuration():
    advance = compute_clock_advance(
        current_minute=50,
        last_wall_at=100,
        wall_now=90,
        game_minutes_per_real_minute=5,
        catchup_cap_minutes=480,
    )
    assert advance.to_minute == 50
    assert day_phase(5 * 60) == "dawn"


def test_deliberation_schedule_is_stable_sparse_and_distributed():
    first = deliberation_minutes("edda-marr", 7)
    second = deliberation_minutes("edda-marr", 7)
    assert first == second
    assert len(first) == deliberation_count("edda-marr", 7)
    assert 3 <= len(first) <= 6
    assert len(set(first)) == len(first)
    assert all(7 * MINUTES_PER_DAY + 6 * 60 <= minute <=
               7 * MINUTES_PER_DAY + 22 * 60 for minute in first)


def test_due_and_next_deliberations_cross_day_boundaries():
    day_zero = deliberation_minutes("wren-no-house", 0)
    assert due_deliberations(
        "wren-no-house",
        after_minute=-1,
        through_minute=day_zero[0],
    ) == (day_zero[0],)
    assert next_deliberation("wren-no-house", day_zero[-1]) > day_zero[-1]


def test_intention_utility_and_tie_breaking_are_deterministic():
    choices = [
        GoalCandidate(
            key="work",
            intention=IntentionKind.WORK,
            base_priority=30,
            commitment_cost=5,
        ),
        GoalCandidate(
            key="warn_maud",
            intention=IntentionKind.SEEK_PERSON,
            base_priority=10,
            relationship_pressure=20,
            target_id="maud-oakrun-orchard",
        ),
    ]
    chosen = choose_intention(choices)
    assert chosen is not None
    assert chosen.goal_key == "warn_maud"
    assert chosen.target_id == "maud-oakrun-orchard"

    tied = [
        GoalCandidate("z", IntentionKind.WORK, 5),
        GoalCandidate("a", IntentionKind.REST, 5),
    ]
    assert choose_intention(tied).goal_key == "a"


def test_need_pressure_becomes_a_private_goal():
    assert need_candidate(
        need="rest", value=90, threshold=50, destination_room_id=1
    ) is None
    candidate = need_candidate(
        need="rest", value=10, threshold=50, destination_room_id=1
    )
    assert candidate is not None
    assert candidate.intention is IntentionKind.REST
    assert candidate.need_pressure == 80


def test_programmatic_route_prefers_time_then_danger():
    edges = [
        RouteEdge(1, 2, travel_minutes=10, danger=4),
        RouteEdge(2, 4, travel_minutes=10, danger=4),
        RouteEdge(1, 3, travel_minutes=10, danger=0),
        RouteEdge(3, 4, travel_minutes=10, danger=1, mode="carriage",
                  service_id="amber-line"),
    ]
    route = shortest_route(edges, from_room_id=1, to_room_id=4)
    assert route is not None
    assert route.room_ids == (1, 3, 4)
    assert route.travel_minutes == 20
    assert route.danger == 1
    event = next_travel_event(route, current_room_id=1, depart_at=100)
    assert event == {
        "kind": "npc_arrive_room",
        "due_at": 110,
        "from_room_id": 1,
        "to_room_id": 3,
        "mode": "walk",
        "service_id": None,
        "danger": 0,
    }


def test_route_can_respect_a_persons_danger_tolerance():
    edges = [
        RouteEdge(1, 2, danger=9),
        RouteEdge(1, 3, danger=2),
        RouteEdge(3, 2, danger=2),
    ]
    route = shortest_route(
        edges, from_room_id=1, to_room_id=2, avoid_danger_above=5
    )
    assert route.room_ids == (1, 3, 2)
    assert shortest_route(
        [RouteEdge(1, 2, danger=9)],
        from_room_id=1,
        to_room_id=2,
        avoid_danger_above=5,
    ) is None


def _memory(
    memory_id: str,
    *,
    tags=frozenset({"rot"}),
    importance=5,
    confidence=1,
    occurred_at=100,
    secrecy=0,
    cascade_depth=0,
):
    return Memory(
        id=memory_id,
        owner_id="rowan-oakrun-courier",
        kind="observation",
        summary="The northern report begins after the damage was complete.",
        tags=tags,
        importance=importance,
        confidence=confidence,
        occurred_at=occurred_at,
        secrecy=secrecy,
        cascade_depth=cascade_depth,
    )


def test_memory_retrieval_balances_relevance_importance_and_recency():
    relevant = _memory("relevant", tags=frozenset({"rot", "north"}))
    trivial = _memory("trivial", tags=frozenset({"supper"}), importance=1)
    assert memory_score(
        relevant, query_tags=frozenset({"rot"}), now_minute=200
    ) > memory_score(
        trivial, query_tags=frozenset({"rot"}), now_minute=200
    )
    assert retrieve_memories(
        [trivial, relevant],
        query_tags=frozenset({"rot"}),
        now_minute=200,
        limit=1,
    ) == (relevant,)


def test_reflection_is_evidence_bounded_and_dedupes_by_day_and_subject():
    evidence = [
        _memory(
            f"rot-evidence-{index}",
            tags=frozenset({"rot", tag}),
            importance=7,
            occurred_at=100 + index,
        )
        for index, tag in enumerate(("water", "road", "names"))
    ]
    reflection = synthesize_reflection(
        evidence,
        owner_id="rowan-oakrun-courier",
        world_minute=600,
    )
    assert reflection is not None
    assert reflection.kind == "reflection"
    assert reflection.id == "reflection:rowan-oakrun-courier:0:rot"
    assert reflection.source_memory_id in {item.id for item in evidence}
    assert reflection.tags == frozenset({"reflection", "rot"})
    assert synthesize_reflection(
        evidence[:2],
        owner_id="rowan-oakrun-courier",
        world_minute=600,
    ) is None


def test_retold_reflections_cannot_amplify_memory_text_without_bound():
    evidence = [
        _memory(
            f"root-evidence-{index}",
            tags=frozenset({"rot", tag}),
            importance=7,
            occurred_at=100 + index,
        )
        for index, tag in enumerate(("water", "road", "names"))
    ]
    reflection = synthesize_reflection(
        evidence,
        owner_id="rowan-oakrun-courier",
        world_minute=600,
    )
    assert reflection is not None

    for day in range(1, 11):
        retellings = [
            transmit_rumour(
                reflection,
                receiver_id=f"listener-{index}",
                speaker_id="rowan-oakrun-courier",
                world_minute=day * 1_440 + index,
                precision=0.9,
            )
            for index in range(2)
        ]
        reflection = synthesize_reflection(
            [*retellings, *evidence],
            owner_id="rowan-oakrun-courier",
            world_minute=day * 1_440 + 100,
        )
        assert reflection is not None
        assert len(reflection.summary) < 500


def test_conversation_respects_secrecy_and_rumour_provenance():
    public = _memory("public", secrecy=0.1)
    secret = _memory("secret", secrecy=0.9, importance=10)
    shared = select_conversation_memories(
        [secret, public],
        topic_tags=frozenset({"rot"}),
        now_minute=200,
        trust=-80,
    )
    assert shared == (public,)

    rumour = transmit_rumour(
        public,
        receiver_id="elowen-wayfarers-rest",
        speaker_id="rowan-oakrun-courier",
        world_minute=220,
        precision=0.9,
    )
    assert rumour.owner_id == "elowen-wayfarers-rest"
    assert rumour.kind == "rumour"
    assert rumour.source_id == "rowan-oakrun-courier"
    assert rumour.source_memory_id == public.id
    assert rumour.cascade_depth == 1
    assert rumour.confidence < public.confidence


def test_rumour_cascade_depth_is_bounded():
    deep = _memory("old-rumour", cascade_depth=3)
    assert select_conversation_memories(
        [deep],
        topic_tags=frozenset({"rot"}),
        now_minute=200,
        trust=100,
        max_cascade_depth=3,
    ) == ()


def test_relationships_are_directional_bounded_and_human_readable():
    one_way = apply_relationship_delta(
        Relationship(familiarity=20),
        trust=150,
        affinity=80,
        familiarity=200,
    )
    assert one_way.trust == 100
    assert one_way.familiarity == 100
    assert bond_word(one_way) == "devoted"
    assert bond_word(Relationship(familiarity=2)) == "unfamiliar"
    assert bond_word(Relationship(trust=-40, fear=70)) == "wary"


def _trigger_context():
    return TriggerContext(
        world_minute=500,
        facts={"rot_seen": True},
        npc_alive={"edda-marr": True},
        npc_rooms={"edda-marr": 4},
        relationships={"edda-marr:wren-no-house:trust": 75},
        known_memory_tags=frozenset({("edda-marr", "black_silt")}),
        occurred_events=frozenset({"carriage_arrived"}),
        rumour_confidence={"rot_started_in_veyr": 0.7},
    )


def test_closed_trigger_ast_composes_without_eval():
    condition = {
        "all": [
            {"kind": "fact_equals", "key": "rot_seen", "value": True},
            {"kind": "npc_alive", "npc_id": "edda-marr", "value": True},
            {
                "any": [
                    {
                        "kind": "memory_tag_known",
                        "npc_id": "edda-marr",
                        "tag": "black_silt",
                    },
                    {"kind": "world_minute_at_least", "value": 999},
                ]
            },
        ]
    }
    assert evaluate_condition(condition, _trigger_context())


def test_unknown_trigger_predicates_and_effects_fail_closed():
    try:
        evaluate_condition({"kind": "__import__"}, _trigger_context())
        raise AssertionError("unknown predicate should fail")
    except ValueError:
        pass
    try:
        validate_effect({"kind": "execute_python"})
        raise AssertionError("unknown effect should fail")
    except ValueError:
        pass
    validate_effect({"kind": "create_rumour"})
