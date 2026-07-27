"""Version-controlled authored content loader.

JSON under ``content/`` is the authoring source.  The database remains the
runtime store: authored definitions are synced into it, while generated rooms,
generated NPCs, and player-mutated state live there directly.
"""
from __future__ import annotations

import json
from pathlib import Path


CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content"
PUBLIC_ROOT = CONTENT_ROOT.parent / "frontend-react" / "public"


def load_json(relative_path: str) -> dict | list:
    path = CONTENT_ROOT / relative_path
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing authored content file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"invalid authored content JSON in {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc


def load_region(relative_manifest: str) -> dict:
    """Load a version-controlled region manifest and its per-room files.

    Manifests keep graph topology small while room terrain and placements live
    one-room-per-file. The returned shape intentionally matches the former
    monolithic world document so runtime synchronization stays format-agnostic.
    """
    manifest_path = Path(relative_manifest)
    raw = load_json(relative_manifest)
    if not isinstance(raw, dict):
        raise RuntimeError(f"{relative_manifest} must contain an object")
    if raw.get("schema_version") != 1:
        raise RuntimeError(f"{relative_manifest} needs schema_version 1")
    region_id = raw.get("id")
    start_room = raw.get("start_room")
    room_paths = raw.get("rooms")
    connections = raw.get("connections")
    if not isinstance(region_id, str) or not region_id:
        raise RuntimeError(f"{relative_manifest}.id must be a non-empty string")
    if not isinstance(start_room, str) or not start_room:
        raise RuntimeError(f"{relative_manifest}.start_room must be a non-empty string")
    if not isinstance(room_paths, list) or not room_paths:
        raise RuntimeError(f"{relative_manifest}.rooms must be a non-empty list")
    if not isinstance(connections, list):
        raise RuntimeError(f"{relative_manifest}.connections must be a list")

    rooms: dict[str, dict] = {}
    base = manifest_path.parent
    for index, room_relative in enumerate(room_paths):
        if not isinstance(room_relative, str) or not room_relative:
            raise RuntimeError(f"{relative_manifest}.rooms[{index}] must be a path")
        path = base / room_relative
        room = load_json(path.as_posix())
        if not isinstance(room, dict):
            raise RuntimeError(f"{path.as_posix()} must contain an object")
        room_id = room.get("id")
        if not isinstance(room_id, str) or not room_id:
            raise RuntimeError(f"{path.as_posix()}.id must be a non-empty string")
        if room_id in rooms:
            raise RuntimeError(f"region {region_id!r} repeats room id {room_id!r}")
        rooms[room_id] = room

    if start_room not in rooms:
        raise RuntimeError(
            f"region {region_id!r} start_room {start_room!r} is not in its room list"
        )
    for index, connection in enumerate(connections):
        if not isinstance(connection, dict):
            raise RuntimeError(
                f"{relative_manifest}.connections[{index}] must be an object"
            )
        source, target = connection.get("from"), connection.get("to")
        if source not in rooms or target not in rooms:
            raise RuntimeError(
                f"region {region_id!r} connection {index} references an unknown room"
            )

    return {
        "schema_version": 1,
        "id": region_id,
        "start_room": start_room,
        "rooms": rooms,
        "connections": connections,
    }


def load_catalog(relative_path: str, *, key_field: str = "id") -> dict[str, dict]:
    """Load a list of definitions and index it by a unique string key."""
    raw = load_json(relative_path)
    if not isinstance(raw, list):
        raise RuntimeError(f"{relative_path} must contain a JSON list")

    indexed: dict[str, dict] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise RuntimeError(f"{relative_path}[{index}] must be an object")
        content_id = entry.get(key_field)
        if not isinstance(content_id, str) or not content_id:
            raise RuntimeError(
                f"{relative_path}[{index}].{key_field} must be a non-empty string"
            )
        if content_id in indexed:
            raise RuntimeError(f"duplicate id {content_id!r} in {relative_path}")
        indexed[content_id] = entry
    return indexed


def require_art_path(value, what: str) -> str:
    """Validate a public art URL and confirm its file ships with the client."""
    if not isinstance(value, str) or not value.startswith("/art/"):
        raise RuntimeError(f"{what} needs a trusted /art/ image path")
    path = PUBLIC_ROOT / value.removeprefix("/")
    if not path.is_file():
        raise RuntimeError(f"{what} references missing art file: {path}")
    return value
