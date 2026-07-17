"""Procedural room generation.

One contract, many techniques. Every generator — whatever algorithm it uses
inside — emits the SAME room dict your seeds use (`backend/seeds.py`) and must
pass the SAME gate the game uses (`backend/room_validation.validate_room`).
That shared contract is the whole design: the harness, the game, and (later)
an AI config-picker all speak room-dict, and none of them can produce a room
the engine would choke on.

A "room type" is a PRESET, not a class and not a technique (mirrors the
"axes, not taxonomy" rule in backend/brains.py): a named bundle of
(generator function, tunable param schema, defaults). Swap the technique behind
a preset and nothing downstream changes, because the output contract didn't.

Public surface:
    list_types()               -> [Preset metadata] for a UI dropdown / AI menu
    schema_for(room_type)      -> [Param] describing the knobs (drives the form)
    generate(room_type, params, seed) -> GenResult (validated room dict or error)
"""
from backend.procgen.registry import generate, list_types, schema_for

__all__ = ["generate", "list_types", "schema_for"]
