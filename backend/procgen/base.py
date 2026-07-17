"""The shared vocabulary every generator speaks: the param schema, the preset
record, param coercion, and the validate-against-the-real-gate helper.

The param schema does triple duty on purpose — this is the elegant bit:
  1. it renders the harness UI form (each Param -> one input),
  2. it coerces/clamps untrusted input (a person turning knobs, or later an LLM
     emitting a config), and
  3. it is exactly the "closed vocabulary" an AI config-picker will fill,
     the same shape as your dialogue-effects channel: propose within the schema,
     the engine disposes of anything out of range.
"""
from dataclasses import dataclass, field
from typing import Callable, Literal
import random

# The REAL gate the game uses — imported, never reimplemented. If the harness
# validated against a copy it would drift and green-light rooms that crash the
# game. One validator, one source of truth.
from backend.room_validation import validate_enemy_refs, validate_room
from backend.seeds import ENEMY_DEFS

# The enemy catalog a generator may place. Single source of truth: the same
# list the world is seeded from, so a generated enemy_id is always real.
ENEMY_IDS = tuple(d["id"] for d in ENEMY_DEFS)
ENEMY_NAMES = {d["id"]: d["name"] for d in ENEMY_DEFS}


ParamKind = Literal["int", "choice", "bool"]


@dataclass(frozen=True)
class Param:
    """One tunable knob. `kind` picks the input type; `min`/`max` bound ints and
    `options` enumerate a choice. `help` is the one-line hint shown in the UI."""
    name: str
    kind: ParamKind
    label: str
    default: object
    min: int | None = None
    max: int | None = None
    options: tuple[str, ...] = ()
    help: str = ""

    def to_json(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "label": self.label,
            "default": self.default, "min": self.min, "max": self.max,
            "options": list(self.options), "help": self.help,
        }

    def coerce(self, value: object) -> object:
        """Force `value` into this param's domain — clamp ints, reject unknown
        choices, fall back to the default. Untrusted input (UI or AI) never
        reaches a generator un-sanitized."""
        if self.kind == "int":
            try:
                v = int(value)
            except (TypeError, ValueError):
                return self.default
            if self.min is not None:
                v = max(self.min, v)
            if self.max is not None:
                v = min(self.max, v)
            return v
        if self.kind == "bool":
            return bool(value)
        if self.kind == "choice":
            return value if value in self.options else self.default
        return self.default


# A generator is a pure function of (coerced params, seeded RNG) -> room dict.
# It takes its own Random so a seed reproduces a room exactly, and so the caller
# owns retry/seeding policy. It must NOT import or touch the DB or live state.
Generator = Callable[[dict, random.Random], dict]


@dataclass(frozen=True)
class Preset:
    """A room type: technique + its knobs + defaults, addressed by `key`.
    `technique` names the industry algorithm the preset demonstrates — shown in
    the harness so you can connect what you see to what generated it."""
    key: str
    label: str
    description: str
    generator: Generator
    params: tuple[Param, ...] = field(default_factory=tuple)
    technique: str = ""

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.params}

    def coerce_params(self, raw: dict | None) -> dict:
        """Return a full param dict: every knob present, every value in-domain.
        Missing keys take the default; extra keys are dropped."""
        raw = raw or {}
        return {p.name: p.coerce(raw.get(p.name, p.default)) for p in self.params}


@dataclass
class GenResult:
    """Outcome of a generate call. `ok` means the room passed the real gate.
    `attempts` records how many rolls it took (generate-and-test visibility),
    and `error` carries the validator's message on failure — the same string
    you'd feed an LLM as a repair prompt."""
    ok: bool
    room: dict | None
    seed: int
    attempts: int
    room_type: str
    params: dict
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "ok": self.ok, "room": self.room, "seed": self.seed,
            "attempts": self.attempts, "room_type": self.room_type,
            "params": self.params, "error": self.error,
        }


def validate(room: dict) -> str | None:
    """Run the room through the REAL game gate. Returns None if valid, else the
    validator's error message. This is the trust boundary: a generator is only
    as trusted as this function is strict."""
    try:
        validate_room(room)
        validate_enemy_refs(room, set(ENEMY_IDS))
    except ValueError as e:
        return str(e)
    return None
