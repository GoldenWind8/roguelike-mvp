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
  party_policy  hint the LLM sees when deciding join_party — SHAPES what it
                proposes (soft, prompt-side)

Optional:
  grants        the per-NPC effect allowlist (NPCS.md, deferred from M5 to
                here). The closed set of GRANTABLE effects this NPC may
                propose — a HARD engine gate, not a hint. Absent/empty means
                "grants nothing": the default, so an ordinary NPC can never be
                recruited no matter what a jailbroken LLM proposes. Only
                effects that aren't universally in-context need listing:
                set_disposition (an NPC changing its OWN mood) is always
                allowed and is deliberately NOT grantable here.
"""
from backend.entities import Disposition

# Effects an NPC may be granted the capability to propose. set_disposition is
# absent on purpose (universally in-context — see the docstring). Closed like
# the effect vocabulary itself: a persona listing anything else fails the gate.
GRANTABLE_EFFECTS = frozenset({"join_party"})

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

    # `grants` is optional; when present it must be a list drawn from the closed
    # grantable set (same law as effects — no smuggling a novel capability past
    # the gate). Absent means the safe default: this NPC grants nothing.
    grants = doc.get("grants", [])
    if not isinstance(grants, list):
        raise ValueError(f"persona 'grants' must be a list, got {grants!r}")
    unknown_grants = set(grants) - GRANTABLE_EFFECTS
    if unknown_grants:
        raise ValueError(
            f"persona 'grants' has effects outside the grantable set "
            f"{sorted(GRANTABLE_EFFECTS)}: {sorted(unknown_grants)}"
        )

    unknown = set(doc) - set(_REQUIRED_STR) - set(_REQUIRED_STR_LIST) - {"disposition", "grants"}
    if unknown:
        # Closed for now, like the effect vocabulary: a generator inventing
        # fields should fail loudly, not smuggle data past the gate.
        raise ValueError(f"persona has unknown fields: {sorted(unknown)}")
