"""Sparse, deterministic NPC deliberation scheduling."""

from __future__ import annotations

import hashlib

from backend.living_world.clock import MINUTES_PER_DAY

MIN_DELIBERATIONS_PER_DAY = 3
MAX_DELIBERATIONS_PER_DAY = 6

# Ordinary people think between waking and late evening. Emergency
# observations can still force a reconsideration outside these windows.
FIRST_DELIBERATION_MINUTE = 6 * 60
LAST_DELIBERATION_MINUTE = 22 * 60


def _stable_u64(*parts: object) -> int:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(
        hashlib.blake2b(material, digest_size=8).digest(), "big"
    )


def deliberation_count(content_id: str, world_day: int) -> int:
    """Choose three to six windows reproducibly for this person and day."""
    if not content_id:
        raise ValueError("content_id must be non-empty")
    if world_day < 0:
        raise ValueError("world_day must be non-negative")
    span = MAX_DELIBERATIONS_PER_DAY - MIN_DELIBERATIONS_PER_DAY + 1
    return MIN_DELIBERATIONS_PER_DAY + (
        _stable_u64("count", content_id, world_day) % span
    )


def deliberation_minutes(content_id: str, world_day: int) -> tuple[int, ...]:
    """Return stable absolute world minutes for one NPC's daily thoughts.

    Windows are distributed across the waking day with a small stable jitter,
    so the whole town does not reconsider its life on the same server tick.
    """
    count = deliberation_count(content_id, world_day)
    waking_span = LAST_DELIBERATION_MINUTE - FIRST_DELIBERATION_MINUTE
    bucket = waking_span / count
    result: list[int] = []
    for index in range(count):
        bucket_start = FIRST_DELIBERATION_MINUTE + int(index * bucket)
        bucket_end = FIRST_DELIBERATION_MINUTE + int((index + 1) * bucket)
        width = max(1, bucket_end - bucket_start)
        jitter = _stable_u64(
            "window", content_id, world_day, index
        ) % width
        local_minute = min(
            LAST_DELIBERATION_MINUTE,
            bucket_start + int(jitter),
        )
        result.append(world_day * MINUTES_PER_DAY + local_minute)
    return tuple(sorted(result))


def due_deliberations(
    content_id: str,
    *,
    after_minute: int,
    through_minute: int,
) -> tuple[int, ...]:
    """Return windows in ``(after_minute, through_minute]``."""
    if after_minute < -1 or through_minute < 0:
        raise ValueError("world minutes are invalid")
    if through_minute <= after_minute:
        return ()

    first_day = max(0, after_minute + 1) // MINUTES_PER_DAY
    last_day = through_minute // MINUTES_PER_DAY
    due: list[int] = []
    for day in range(first_day, last_day + 1):
        due.extend(
            minute
            for minute in deliberation_minutes(content_id, day)
            if after_minute < minute <= through_minute
        )
    return tuple(due)


def next_deliberation(content_id: str, after_minute: int) -> int:
    """Find the first ordinary deliberation strictly after a world minute."""
    if after_minute < -1:
        raise ValueError("after_minute is invalid")
    day = max(0, after_minute + 1) // MINUTES_PER_DAY
    while True:
        for minute in deliberation_minutes(content_id, day):
            if minute > after_minute:
                return minute
        day += 1
