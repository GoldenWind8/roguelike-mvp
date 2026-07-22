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
