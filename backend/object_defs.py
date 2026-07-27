"""Trusted object and building definitions loaded from authored JSON.

Rooms store only a stable type id plus placement. Collision and presentation
come from ``content/objects.json`` and ``content/buildings.json`` so generated
rooms cannot invent either.
"""
from dataclasses import dataclass

from backend.content import load_catalog, require_art_path


Cell = tuple[int, int]


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


def _definition(entry: dict) -> ObjectDefinition:
    footprint = entry.get("footprint", [[0, 0]])
    size = entry.get("visual_size", [1, 1])
    details = entry.get("details", [])
    image = entry.get("image")
    interaction = entry.get("interaction")
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
    if interaction not in (None, "shop", "noticeboard", "carriage"):
        raise RuntimeError(f"object {entry.get('id')!r} has invalid interaction {interaction!r}")
    return ObjectDefinition(
        id=entry["id"], label=entry["label"], description=entry["description"],
        details=tuple(details), footprint=tuple(tuple(cell) for cell in footprint),
        blocks_movement=entry.get("blocks_movement", True), image=image,
        visual_size=(size[0], size[1]), interaction=interaction,
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
