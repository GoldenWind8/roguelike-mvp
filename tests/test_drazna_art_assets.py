"""Fast integrity checks for Drazna's shipped production WebP cutouts.

This validates the RIFF/chunk structure of the delivery assets without
scanning every alpha pixel. Full alpha-bound inspection belongs to the asset
acceptance pass; the regular backend suite only needs a cheap broken-link,
broken-image, and accidental-source-size guard.
"""

from __future__ import annotations

import json
from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = ROOT / "frontend-react" / "public"


def _image_urls(value) -> set[str]:
    if isinstance(value, dict):
        urls = {
            image
            for key, image in value.items()
            if key == "image" and isinstance(image, str)
        }
        for child in value.values():
            urls.update(_image_urls(child))
        return urls
    if isinstance(value, list):
        urls: set[str] = set()
        for child in value:
            urls.update(_image_urls(child))
        return urls
    return set()


def _drazna_art_references() -> dict[str, set[str]]:
    references: dict[str, set[str]] = {}

    for catalog in PUBLIC_ROOT.rglob("*.asset.json"):
        if "drazna" not in {part.lower() for part in catalog.parts}:
            continue
        payload = json.loads(catalog.read_text(encoding="utf-8"))
        for url in _image_urls(payload):
            references.setdefault(url, set()).add(str(catalog.relative_to(ROOT)))

    for relative in (
        "content/actors.json",
        "content/enemies.json",
        "content/objects.json",
    ):
        path = ROOT / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        for url in _image_urls(payload):
            if "/drazna/" in url:
                references.setdefault(url, set()).add(relative)

    return references


def _decode_webp_payload(path: Path) -> tuple[int, int, bool]:
    """Validate an extended WebP container; return width, height, alpha."""
    data = path.read_bytes()
    assert len(data) > 1024, f"{path} is unexpectedly small"
    assert data[:4] == b"RIFF", f"{path} has no RIFF signature"
    assert data[8:12] == b"WEBP", f"{path} has no WebP signature"
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8, (
        f"{path} has an inconsistent RIFF size"
    )

    offset = 12
    chunks: dict[bytes, list[bytes]] = {}
    while offset < len(data):
        assert offset + 8 <= len(data), f"{path} has a truncated chunk header"
        chunk_type = data[offset:offset + 4]
        length = struct.unpack("<I", data[offset + 4:offset + 8])[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        assert chunk_end <= len(data), f"{path} has a truncated {chunk_type!r} chunk"
        chunks.setdefault(chunk_type, []).append(data[chunk_start:chunk_end])
        offset = chunk_end + (length & 1)
    assert offset == len(data), f"{path} has invalid RIFF padding"

    assert b"VP8X" in chunks, f"{path} is missing its extended header"
    header = chunks[b"VP8X"][0]
    assert len(header) == 10, f"{path} has an invalid VP8X header"
    width = 1 + int.from_bytes(header[4:7], "little")
    height = 1 + int.from_bytes(header[7:10], "little")
    has_alpha = bool(header[0] & 0x10)
    assert width * height > 64, f"{path} has no meaningful pixel area"
    assert b"VP8 " in chunks or b"VP8L" in chunks, f"{path} has no image payload"
    assert has_alpha and (b"ALPH" in chunks or b"VP8L" in chunks), (
        f"{path} has no transparent map-token payload"
    )
    return width, height, has_alpha


def test_all_drazna_catalog_art_urls_resolve_to_valid_images():
    references = _drazna_art_references()
    assert len(references) == 40
    assert {
        "/art/world/enemies/drazna/flood-hollow-warden-v1.webp",
        "/art/world/enemies/drazna/silt-drowned-ferryman-v1.webp",
        "/art/world/enemies/drazna/siltbound-salvager-v1.webp",
        "/art/world/enemies/drazna/black-silt-leech-colony-v1.webp",
        "/art/world/enemies/drazna/sluicebound-gate-seven-v1.webp",
        "/art/world/objects/drazna/gate-seven-chain-drum-v1.webp",
        "/art/world/objects/drazna/crown-ledger-plinth-v1.webp",
        "/art/world/objects/drazna/first-record-memorial-v1.webp",
    } <= references.keys()

    for url, sources in sorted(references.items()):
        assert url.startswith("/art/"), f"{url!r} in {sorted(sources)} is not public art"
        assert url.endswith(".webp"), (
            f"{url!r} in {sorted(sources)} is not an optimized WebP delivery asset"
        )
        path = PUBLIC_ROOT / url.removeprefix("/")
        assert path.is_file(), f"{url!r} referenced by {sorted(sources)} is missing"
        _decode_webp_payload(path)
        assert path.stat().st_size < 512 * 1024, (
            f"{path} is too heavy for a live game cutout"
        )


def test_sluicebound_actor_uses_the_live_persona_art_id():
    actors = json.loads((ROOT / "content" / "actors.json").read_text(encoding="utf-8"))
    actor = next(entry for entry in actors if entry["id"] == "sluicebound_gate_seven")
    assert actor == {
        "id": "sluicebound_gate_seven",
        "image": "/art/world/enemies/drazna/sluicebound-gate-seven-v1.webp",
        "visual_size": [2, 3],
    }
