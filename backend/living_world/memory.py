"""Memory retrieval and bounded NPC-to-NPC rumour transmission."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Iterable


@dataclass(frozen=True)
class Memory:
    id: str
    owner_id: str
    kind: str
    summary: str
    tags: frozenset[str]
    importance: float
    confidence: float
    occurred_at: int
    last_recalled_at: int | None = None
    source_id: str | None = None
    source_memory_id: str | None = None
    shareable: bool = True
    secrecy: float = 0
    cascade_depth: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.importance <= 10:
            raise ValueError("memory importance must be within 0..10")
        if not 0 <= self.confidence <= 1:
            raise ValueError("memory confidence must be within 0..1")
        if not 0 <= self.secrecy <= 1:
            raise ValueError("memory secrecy must be within 0..1")
        if self.cascade_depth < 0:
            raise ValueError("cascade_depth must be non-negative")


def memory_score(
    memory: Memory,
    *,
    query_tags: frozenset[str],
    now_minute: int,
    relationship_salience: float = 0,
    unresolved_promise: bool = False,
) -> float:
    if now_minute < memory.occurred_at:
        age = 0
    else:
        age = now_minute - memory.occurred_at
    overlap = len(memory.tags & query_tags)
    union = len(memory.tags | query_tags)
    relevance = overlap / union if union else 0
    # Half-life of roughly one world day; old high-importance memories remain.
    recency = math.exp(-age / 1440)
    return (
        relevance * 5
        + recency * 2
        + memory.importance
        + relationship_salience
        + (2 if unresolved_promise else 0)
    )


def retrieve_memories(
    memories: Iterable[Memory],
    *,
    query_tags: frozenset[str],
    now_minute: int,
    limit: int = 8,
) -> tuple[Memory, ...]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    ranked = sorted(
        memories,
        key=lambda memory: (
            -memory_score(
                memory,
                query_tags=query_tags,
                now_minute=now_minute,
            ),
            -memory.occurred_at,
            memory.id,
        ),
    )
    return tuple(ranked[:limit])


def synthesize_reflection(
    memories: Iterable[Memory],
    *,
    owner_id: str,
    world_minute: int,
    minimum_importance: float = 18.0,
) -> Memory | None:
    """Cheaply turn accumulated experience into one higher-level belief.

    This is the deterministic half of the observation/plan/reflection loop.
    It never invents evidence: the reflection cites the most important
    non-reflection memories and carries only tags they already contained.
    """
    evidence = sorted(
        (memory for memory in memories if memory.kind != "reflection"),
        key=lambda memory: (-memory.importance, -memory.occurred_at, memory.id),
    )[:4]
    if len(evidence) < 3 or sum(item.importance for item in evidence) < minimum_importance:
        return None
    generic = {"person", "conversation", "observation", "memory"}
    weighted_tags: dict[str, float] = {}
    for item in evidence:
        for tag in item.tags - generic:
            weighted_tags[tag] = weighted_tags.get(tag, 0.0) + item.importance
    if not weighted_tags:
        return None
    subject = min(
        weighted_tags,
        key=lambda tag: (-weighted_tags[tag], tag),
    )
    day = world_minute // 1440
    first, second = evidence[:2]
    return Memory(
        id=f"reflection:{owner_id}:{day}:{subject}",
        owner_id=owner_id,
        kind="reflection",
        summary=(
            f"A pattern around {subject.replace('_', ' ')} connects "
            f"“{first.summary}” with “{second.summary}”."
        ),
        tags=frozenset({"reflection", subject}),
        importance=min(10.0, 5.0 + weighted_tags[subject] / 8.0),
        confidence=min(
            1.0,
            sum(item.confidence for item in evidence) / len(evidence),
        ),
        occurred_at=world_minute,
        source_memory_id=first.id,
        secrecy=max(item.secrecy for item in evidence),
    )


def select_conversation_memories(
    memories: Iterable[Memory],
    *,
    topic_tags: frozenset[str],
    now_minute: int,
    trust: float,
    max_items: int = 3,
    max_cascade_depth: int = 3,
) -> tuple[Memory, ...]:
    """Pick sourced facts this speaker is willing and able to share."""
    willingness = max(0.0, min(1.0, (trust + 100) / 200))
    allowed_secrecy = 0.15 + willingness * 0.75
    eligible = (
        memory
        for memory in memories
        if memory.shareable
        and memory.secrecy <= allowed_secrecy
        and memory.cascade_depth < max_cascade_depth
    )
    return retrieve_memories(
        eligible,
        query_tags=topic_tags,
        now_minute=now_minute,
        limit=max_items,
    )


def transmit_rumour(
    memory: Memory,
    *,
    receiver_id: str,
    speaker_id: str,
    world_minute: int,
    precision: float,
    confidence_factor: float = 0.9,
) -> Memory:
    """Create a receiver-owned sourced rumour without changing truth."""
    if not 0 <= precision <= 1:
        raise ValueError("precision must be within 0..1")
    if not 0 <= confidence_factor <= 1:
        raise ValueError("confidence_factor must be within 0..1")
    confidence = memory.confidence * confidence_factor * (0.65 + 0.35 * precision)
    return replace(
        memory,
        id=f"rumour:{receiver_id}:{speaker_id}:{memory.id}:{world_minute}",
        owner_id=receiver_id,
        kind="rumour",
        confidence=max(0.0, min(1.0, confidence)),
        occurred_at=world_minute,
        last_recalled_at=None,
        source_id=speaker_id,
        source_memory_id=memory.id,
        cascade_depth=memory.cascade_depth + 1,
    )
