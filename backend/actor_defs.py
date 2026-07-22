"""Trusted actor presentation loaded from version-controlled content JSON.

Persistent actors select ids from ``content/actors.json``. Respawnable enemy
stats and art live together in ``content/enemies.json``. Neither personas nor
generated rooms may inject arbitrary URLs or scales.
"""
from dataclasses import dataclass

from backend.content import load_catalog, require_art_path


@dataclass(frozen=True)
class ActorArt:
    id: str
    image: str
    visual_size: tuple[int, int] = (1, 2)


def _art(entry: dict, *, default_size=(1, 2)) -> ActorArt:
    image = require_art_path(entry.get("image"), f"actor {entry.get('id')!r}")
    size = entry.get("visual_size", list(default_size))
    if (not isinstance(size, list) or len(size) != 2
            or not all(isinstance(value, int) and value > 0 for value in size)):
        raise RuntimeError(f"actor {entry.get('id')!r} has invalid visual_size {size!r}")
    return ActorArt(str(entry["id"]), image, (size[0], size[1]))


_ACTORS = load_catalog("actors.json")
_ENEMIES = load_catalog("enemies.json", key_field="key")

_ART = {content_id: _art(entry) for content_id, entry in _ACTORS.items()}
_ENEMY_ART: dict[str, str] = {}
for entry in _ENEMIES.values():
    if entry.get("image") is None:
        continue
    art = _art({**entry, "id": entry["key"]})
    if art.id in _ART:
        raise RuntimeError(f"duplicate actor/enemy art id {art.id!r}")
    _ART[art.id] = art
    _ENEMY_ART[entry["name"]] = art.id


def get_actor_art(art_id: str | None) -> ActorArt | None:
    return _ART.get(art_id) if isinstance(art_id, str) else None


def known_actor_art_ids() -> tuple[str, ...]:
    return tuple(_ART)


def enemy_art(name: str) -> ActorArt | None:
    return get_actor_art(_ENEMY_ART.get(name))
