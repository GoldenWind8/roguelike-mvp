"""Persistent game-clock arithmetic with bounded offline catch-up."""

from __future__ import annotations

from dataclasses import dataclass
import math

MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class ClockAdvance:
    """One requested clock advance split into simulated and coalesced time."""

    from_minute: int
    to_minute: int
    simulated_minutes: int
    coalesced_minutes: int
    wall_seconds: float


def compute_clock_advance(
    *,
    current_minute: int,
    last_wall_at: float,
    wall_now: float,
    game_minutes_per_real_minute: float,
    catchup_cap_minutes: int,
) -> ClockAdvance:
    """Convert elapsed wall time to a bounded number of game minutes.

    Whole game minutes are authoritative. Fractional time remains implicit in
    ``last_wall_at`` until a later call has accumulated enough to cross the
    next minute. A backwards wall clock never moves the world backwards.
    """
    if current_minute < 0:
        raise ValueError("current_minute must be non-negative")
    if game_minutes_per_real_minute <= 0:
        raise ValueError("game_minutes_per_real_minute must be positive")
    if catchup_cap_minutes < 0:
        raise ValueError("catchup_cap_minutes must be non-negative")

    wall_seconds = max(0.0, wall_now - last_wall_at)
    requested = math.floor(
        wall_seconds * game_minutes_per_real_minute / 60.0
    )
    simulated = min(requested, catchup_cap_minutes)
    return ClockAdvance(
        from_minute=current_minute,
        to_minute=current_minute + simulated,
        simulated_minutes=simulated,
        coalesced_minutes=max(0, requested - simulated),
        wall_seconds=wall_seconds,
    )


def world_day(world_minute: int) -> int:
    if world_minute < 0:
        raise ValueError("world_minute must be non-negative")
    return world_minute // MINUTES_PER_DAY


def minute_of_day(world_minute: int) -> int:
    if world_minute < 0:
        raise ValueError("world_minute must be non-negative")
    return world_minute % MINUTES_PER_DAY


def day_phase(world_minute: int) -> str:
    minute = minute_of_day(world_minute)
    if minute < 5 * 60:
        return "deep_night"
    if minute < 8 * 60:
        return "dawn"
    if minute < 12 * 60:
        return "morning"
    if minute < 14 * 60:
        return "midday"
    if minute < 18 * 60:
        return "afternoon"
    if minute < 21 * 60:
        return "evening"
    return "night"
