"""Actors in memory (NPCS.md Decision 4: shared shape in memory, separate
tables at rest).

`Actor` carries the one shape combat and occupancy code target — they stop
caring what they hit. Subclasses add only their extras and MUST NOT add
behavior: how an actor decides to act is a brain, how it relates to players
is its `disposition` field. "Enemy" and "NPC" are coordinates on those axes,
not kinds of things — a shopkeeper turning hostile is a field write, never a
change of class.

kw_only lets subclasses override field defaults (e.g. Player's defense)
without hitting the "non-default argument follows default" dataclass trap.
"""
from dataclasses import dataclass, field
from enum import Enum

from backend.config import PLAYER_ATTACK_DAMAGE, PLAYER_DEFENSE


class Disposition(str, Enum):
    """How an actor relates to players. Three values from day one (NPCS.md
    Decision 3) even though v1 barely uses NEUTRAL — this enum is the hook
    escalation and factions grab onto."""
    HOSTILE = "hostile"
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"


@dataclass
class Position:
    x: int
    y: int


@dataclass(kw_only=True)
class Actor:
    id: str
    name: str
    position: Position
    hp: int
    max_hp: int
    defense: int
    attack_damage: int
    is_alive: bool = True
    disposition: Disposition = Disposition.NEUTRAL

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "position": [self.position.x, self.position.y],
            "hp": self.hp,
            "max_hp": self.max_hp,
            "defense": self.defense,
            "attack_damage": self.attack_damage,
            "is_alive": self.is_alive,
            "disposition": self.disposition.value,
        }


# Three actor kinds, and what actually separates them (NPCS.md "axes, not
# taxonomy" + "fungible vs. individual"):
#
#   Player  — an individual driven by a socket, not a brain.
#   NPC     — an INDIVIDUAL driven by a brain chosen from its data. Persists
#             (persona, memory, party) in an `npcs` row; survives resets.
#   Enemy   — a SIMPLE (fungible) actor driven by the same data-chosen brain.
#
# The line between NPC and Enemy is PERSISTENCE, not behavior or hostility: a
# hostile Enemy and an NPC soured to hostile both get the ChaseBrain and act
# identically (see brains.select_brain). An Enemy is, precisely, a hostile NPC
# minus the row — so its hp/position are forgotten on room reset and it respawns
# from the template (a feature, not a gap). Disposition is an orthogonal axis on
# every actor, which is why "shopkeeper turns hostile" is a field write, never a
# change of class.


@dataclass(kw_only=True)
class Player(Actor):
    """A human-controlled actor: its socket decides its moves, so it has no
    brain. Friendly by default (the player side hostiles target)."""
    defense: int = PLAYER_DEFENSE
    attack_damage: int = PLAYER_ATTACK_DAMAGE
    disposition: Disposition = Disposition.FRIENDLY


@dataclass(kw_only=True)
class Enemy(Actor):
    """A SIMPLE, fungible enemy: hostile by default and behaviorally just a
    hostile NPC (same ChaseBrain), but with NO persistent state — no `npcs`
    row, hp/position forgotten on reset, respawned from the template. A row
    exists to remember something; an enemy has nothing worth remembering (until
    it's recruited, at which point it's promoted to an NPC individual)."""
    disposition: Disposition = Disposition.HOSTILE


@dataclass(kw_only=True)
class NPC(Actor):
    """An INDIVIDUAL (NPCS.md "fungible vs. individual"): db_id ties the live
    entity back to its `npcs` row so eviction can save it. `persona` is the
    schema-validated document the dialogue layer reads; `transcript` is the
    bounded dialogue memory that persists with the row. Same actor concept as
    an Enemy — the difference is that this one's state is worth a row."""
    db_id: int
    persona: dict = field(default_factory=dict)
    transcript: list = field(default_factory=list)
    attack_damage: int = 0
    # The one per-player relationship datum in v1 (NPCS.md "Followers"): the id
    # of the player this NPC follows, or None. Kept as party state, NOT a
    # generalized relationship system. Persists on the row across resets.
    party_owner_id: str | None = None

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["role"] = self.persona.get("role", "")
        d["party_owner_id"] = self.party_owner_id
        return d
