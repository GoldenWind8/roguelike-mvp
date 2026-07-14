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


@dataclass(kw_only=True)
class Player(Actor):
    defense: int = PLAYER_DEFENSE
    attack_damage: int = PLAYER_ATTACK_DAMAGE
    disposition: Disposition = Disposition.FRIENDLY


@dataclass(kw_only=True)
class Enemy(Actor):
    disposition: Disposition = Disposition.HOSTILE


@dataclass(kw_only=True)
class NPC(Actor):
    """An individual (NPCS.md "fungible vs. individual"): db_id ties the live
    entity back to its `npcs` row so eviction can save it. `persona` is the
    schema-validated document the dialogue layer reads; `transcript` is the
    bounded dialogue memory that persists with the row."""
    db_id: int
    persona: dict = field(default_factory=dict)
    transcript: list = field(default_factory=list)
    attack_damage: int = 0

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["role"] = self.persona.get("role", "")
        return d
