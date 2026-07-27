"""Deterministic living-world primitives.

The package deliberately contains no network calls. Language models may
propose dialogue or reflections at the application edge, but time, movement,
memory, relationships, triggers, and NPC intentions remain reproducible game
rules.
"""

from backend.living_world.clock import ClockAdvance, compute_clock_advance
from backend.living_world.scheduler import deliberation_minutes

__all__ = [
    "ClockAdvance",
    "compute_clock_advance",
    "deliberation_minutes",
]
