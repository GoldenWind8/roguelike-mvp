"""Trusted object and building definitions loaded from authored JSON.

Rooms store only a stable type id plus placement. Collision and presentation
come from ``content/objects.json`` and ``content/buildings.json`` so generated
rooms cannot invent either.
"""
from dataclasses import dataclass

from backend.content import load_catalog, require_art_path


Cell = tuple[int, int]

# Object-anchored player transfers reserve this complete logical apron. Dormant
# NPC placement uses the same radius around carriage interactions so an
# initially valid coach landing cannot be consumed while its journey runs.
OBJECT_ARRIVAL_APRON_RADIUS = 2


@dataclass(frozen=True)
class ObjectDiscovery:
    """A durable clue learned only when this authored object is inspected."""

    key: str
    title: str
    summary: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObjectDefinition:
    id: str
    label: str
    description: str
    details: tuple[str, ...] = ()
    footprint: tuple[Cell, ...] = ((0, 0),)
    blocks_movement: bool = True
    image: str | None = None
    visual_size: tuple[int, int] = (1, 1)
    interaction: str | None = None
    discovery: ObjectDiscovery | None = None


def _discovery(value: object, object_id: object) -> ObjectDiscovery | None:
    if value is None:
        return None
    path = f"object {object_id!r}.discovery"
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must be an object")
    required = {"key", "title", "summary", "tags"}
    if set(value) != required:
        raise RuntimeError(
            f"{path} must contain exactly {sorted(required)}, got {sorted(value)}"
        )
    key = value["key"]
    title = value["title"]
    summary = value["summary"]
    tags = value["tags"]
    if (
        not isinstance(key, str)
        or not key
        or any(
            not (char.islower() or char.isdigit() or char in "-_:")
            for char in key
        )
    ):
        raise RuntimeError(f"{path}.key must be a safe lowercase identifier")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise RuntimeError(f"{path}.title must be 1..100 characters")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 600:
        raise RuntimeError(f"{path}.summary must be 1..600 characters")
    if (
        not isinstance(tags, list)
        or len(tags) != len(set(tags))
        or any(
            not isinstance(tag, str)
            or not tag
            or any(
                not (char.islower() or char.isdigit() or char in "-_")
                for char in tag
            )
            for tag in tags
        )
    ):
        raise RuntimeError(f"{path}.tags must be a unique identifier list")
    return ObjectDiscovery(
        key=key,
        title=title.strip(),
        summary=summary.strip(),
        tags=tuple(tags),
    )


def _definition(entry: dict) -> ObjectDefinition:
    footprint = entry.get("footprint", [[0, 0]])
    size = entry.get("visual_size", [1, 1])
    details = entry.get("details", [])
    image = entry.get("image")
    interaction = entry.get("interaction")
    discovery = _discovery(entry.get("discovery"), entry.get("id"))
    if (not isinstance(footprint, list) or not footprint
            or any(not isinstance(cell, list) or len(cell) != 2
                   or not all(isinstance(value, int) and value >= 0 for value in cell)
                   for cell in footprint)):
        raise RuntimeError(f"object {entry.get('id')!r} has invalid footprint {footprint!r}")
    if len({tuple(cell) for cell in footprint}) != len(footprint):
        raise RuntimeError(f"object {entry.get('id')!r} repeats a footprint cell")
    if (not isinstance(size, list) or len(size) != 2
            or not all(isinstance(value, int) and value > 0 for value in size)):
        raise RuntimeError(f"object {entry.get('id')!r} has invalid visual_size {size!r}")
    if image is not None:
        image = require_art_path(image, f"object {entry.get('id')!r}")
    if not isinstance(details, list) or not all(isinstance(item, str) for item in details):
        raise RuntimeError(f"object {entry.get('id')!r} has invalid details")
    if interaction not in (None, "shop", "noticeboard", "carriage", "situation"):
        raise RuntimeError(f"object {entry.get('id')!r} has invalid interaction {interaction!r}")
    return ObjectDefinition(
        id=entry["id"], label=entry["label"], description=entry["description"],
        details=tuple(details), footprint=tuple(tuple(cell) for cell in footprint),
        blocks_movement=entry.get("blocks_movement", True), image=image,
        visual_size=(size[0], size[1]), interaction=interaction,
        discovery=discovery,
    )


_RAW = {**load_catalog("objects.json")}
for _id, _entry in load_catalog("buildings.json").items():
    if _id in _RAW:
        raise RuntimeError(f"duplicate object/building id {_id!r}")
    _RAW[_id] = _entry
_DEFINITIONS = {_id: _definition(entry) for _id, entry in _RAW.items()}


def get_object_definition(object_type: str) -> ObjectDefinition | None:
    return _DEFINITIONS.get(object_type)


def known_object_types() -> tuple[str, ...]:
    return tuple(_DEFINITIONS)


def occupied_cells(definition: ObjectDefinition, x: int, y: int) -> tuple[Cell, ...]:
    return tuple((x + dx, y + dy) for dx, dy in definition.footprint)
