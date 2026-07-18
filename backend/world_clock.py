"""The world clock (docs/LOOT.md Decision 3): one global wall-clock time that
runs across every room and both timing modes. Consumable buffs, poison
debuffs — anything with a duration — stores an absolute `expires_at` from this
clock and is pruned lazily wherever stats are read, plus by the coarse ticker
in main.py. No per-effect asyncio timers, ever: hundreds of live timers are a
debugging nightmare; a lazy "is it expired?" check is identical behavior with
zero moving parts.

A module so small it barely earns the file — but it is the ONE place game
code asks what time it is, which makes tests (freeze it with monkeypatch) and
future rethinks (a paused server, a scaled clock) one-line changes.
Deliberately wall time, not monotonic: expiry stamps survive a server restart,
and the buff genuinely burning down while the server was off IS the global
world clock the design wants. Hunger will read this same clock.
"""
import time


def now() -> float:
    """Seconds since the epoch, as the game world experiences it."""
    return time.time()
