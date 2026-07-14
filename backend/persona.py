"""Validate-then-store for persona documents (NPCS.md "Personas As Data").

The gate exists because NPCs will eventually be GENERATED on the fly: a
generated persona must validate before it enters the world — AI proposes,
the engine disposes, applied to content instead of state. Hand-authored
personas pass through the same gate so we know it works before pointing a
generator at it.

Like room_validation, every failure raises ValueError with a message specific
enough to feed straight back to an LLM as a repair prompt.

v1 fields (all required):
  id            stable string slug
  name          display name
  role          one-line function in the world ("caretaker", "guard")
  persona       one paragraph of voice and attitude — what the LLM speaks from
  drives        short list of motivations
  disposition   hostile | neutral | friendly
  canned        fallback lines, >= 1 (the CannedProvider and the live
                fallback both draw from these — an NPC must never be mute)
  party_policy  hint the LLM sees when deciding join_party (unused until the
                party-effects slice, required now so authored content is ready)
"""
from backend.entities import Disposition

_REQUIRED_STR = ("id", "name", "role", "persona", "party_policy")
_REQUIRED_STR_LIST = ("drives", "canned")

# A paragraph, not a novel: the persona is a stable prompt segment, and prompt
# space is a budget. Generous enough for any reasonable authored voice.
MAX_TEXT_LEN = 2000
MAX_LIST_ITEMS = 12


def validate_persona(doc: dict) -> None:
    """Raise ValueError if `doc` is not a valid persona. Returns None on success."""
    if not isinstance(doc, dict):
        raise ValueError(f"persona must be an object, got {type(doc).__name__}")

    for fieldname in _REQUIRED_STR:
        value = doc.get(fieldname)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"persona field '{fieldname}' must be a non-empty string, got {value!r}")
        if len(value) > MAX_TEXT_LEN:
            raise ValueError(f"persona field '{fieldname}' exceeds {MAX_TEXT_LEN} chars")

    for fieldname in _REQUIRED_STR_LIST:
        value = doc.get(fieldname)
        if not isinstance(value, list) or not value:
            raise ValueError(f"persona field '{fieldname}' must be a non-empty list of strings, got {value!r}")
        if len(value) > MAX_LIST_ITEMS:
            raise ValueError(f"persona field '{fieldname}' exceeds {MAX_LIST_ITEMS} items")
        for i, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"persona '{fieldname}[{i}]' must be a non-empty string, got {item!r}")
            if len(item) > MAX_TEXT_LEN:
                raise ValueError(f"persona '{fieldname}[{i}]' exceeds {MAX_TEXT_LEN} chars")

    disposition = doc.get("disposition")
    valid = [d.value for d in Disposition]
    if disposition not in valid:
        raise ValueError(f"persona 'disposition' must be one of {valid}, got {disposition!r}")

    unknown = set(doc) - set(_REQUIRED_STR) - set(_REQUIRED_STR_LIST) - {"disposition"}
    if unknown:
        # Closed for now, like the effect vocabulary: a generator inventing
        # fields should fail loudly, not smuggle data past the gate.
        raise ValueError(f"persona has unknown fields: {sorted(unknown)}")
