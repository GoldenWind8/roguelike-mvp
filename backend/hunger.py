"""The hunger clock (docs/LOOT.md Decision 5) — Minecraft's carrot and Don't
Starve's stick on one meter:

  - the meter drains a little every world tick, ONLY while its owner is
    connected and alive (a player in a live room IS connected — disconnects
    leave the room at the socket edge);
  - WELL FED (>= HUNGER_REGEN_THRESHOLD): wounds slowly knit — 1 hp a tick,
    each hp costing extra hunger on top of the base drain, so a full belly
    is a healing resource you spend;
  - STARVING (0): the meter eats you instead — HUNGER_STARVE_DAMAGE a tick,
    routed through the ordinary Damage effect (its min-1 clamp means armor
    can't make starvation free, and its death handling means it CAN kill).

Pure functions over room state, no asyncio and no I/O — main.world_ticker
owns when this runs; this module owns what a tick MEANS. Both consequences
go through apply_effect like every other mutation: hunger proposes, the
engine disposes, and the events fall out for free.
"""
from backend.config import (
    HUNGER_DRAIN_PER_S,
    HUNGER_REGEN_COST,
    HUNGER_REGEN_THRESHOLD,
    HUNGER_STARVE_DAMAGE,
)
from backend.effects import Damage, Heal, apply_effect
from backend.events import EventType, GameEvent
from backend.inventory import effective_stat


def tick_room_hunger(room, dt: float) -> tuple[list[GameEvent], bool]:
    """Advance every living player's hunger by `dt` seconds. Returns the
    domain events plus a 'visible' flag — true when a rounded meter value
    moved, so the caller broadcasts fresh state even on event-less ticks
    (the client's bar should never freeze between meals)."""
    events: list[GameEvent] = []
    visible = False
    for player in list(room.players.values()):
        if not player.is_alive:
            continue
        before_shown = round(player.hunger)
        was_starving = player.hunger <= 0
        player.hunger = max(0.0, player.hunger - HUNGER_DRAIN_PER_S * dt)

        if player.hunger <= 0:
            if not was_starving:
                events.append(GameEvent(
                    EventType.PLAYER_STARVING, {"target_id": player.id}, room.round,
                ))
            events.extend(apply_effect(
                room,
                Damage(player.id, HUNGER_STARVE_DAMAGE, cause="starvation"),
            ))
        elif (player.hunger >= HUNGER_REGEN_THRESHOLD
                and player.hp < effective_stat(player, "max_hp")):
            events.extend(apply_effect(room, Heal(player.id, 1)))
            player.hunger = max(0.0, player.hunger - HUNGER_REGEN_COST)

        if round(player.hunger) != before_shown:
            visible = True
    return events, visible
