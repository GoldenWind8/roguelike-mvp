"""Authored regional loot that supplements, but does not replace, the world pool.

Regional items use the same closed item/effect vocabulary as every seed and
LLM-minted item.  The bundled URL art is also their stable content marker:
item rows are immutable and predate a dedicated content-id column, so startup
backfill can recognize these definitions without matching (or modifying) an
unrelated player-grown item by name.
"""

DRAZNA_ITEMS: tuple[dict, ...] = (
    {
        "name": "Smoked Eel & Blackbread",
        "rarity": "common",
        "type": "consumable",
        "description": (
            "A paper parcel from the Eel and Ember: smoked river eel over "
            "black rye, salty enough to make old floodwater taste clean."
        ),
        "art": {
            "kind": "url",
            "value": "/art/items/drazna/smoked-eel-blackbread-icon-v1.webp",
        },
        "payload": {
            "effects": [
                {"kind": "restore_hunger", "amount": 28},
                {"kind": "restore_hp", "amount": 3},
            ],
        },
    },
    {
        "name": "Black Silt Sample",
        "rarity": "common",
        "type": "throwable",
        "description": (
            "A sealed Undertide specimen. The black grains recoil from clean "
            "glass, proof of something dangerous but not of where it began."
        ),
        "art": {
            "kind": "url",
            "value": "/art/items/drazna/black-silt-sample-icon-v1.webp",
        },
        "payload": {
            "throw_range": 4,
            "area": {"shape": "radius", "size": 1},
            "effects": [
                {"kind": "damage", "amount": 3},
                {
                    "kind": "stat_mod",
                    "stat": "defense",
                    "amount": -1,
                    "duration_s": 45,
                },
            ],
        },
    },
    {
        "name": "Low Lantern Token",
        "rarity": "common",
        "type": "wearable",
        "description": (
            "A wick-notched brass token passed beneath Drazna's honest "
            "counters. Cold in the palm, it lends a thief's hard composure."
        ),
        "art": {
            "kind": "url",
            "value": "/art/items/drazna/low-lantern-token-icon-v1.webp",
        },
        "payload": {
            "effects": [
                {"kind": "stat_mod", "stat": "max_hp", "amount": 4},
            ],
        },
    },
    {
        "name": "Floodwarden Repair Kit",
        "rarity": "rare",
        "type": "consumable",
        "description": (
            "Waxed cord, iron splints, pitch, and pressure cloth. In practiced "
            "hands it braces a body as readily as tired gatework."
        ),
        "art": {
            "kind": "url",
            "value": "/art/items/drazna/floodwarden-repair-kit-icon-v1.webp",
        },
        "payload": {
            "effects": [
                {"kind": "restore_hp", "amount": 24},
                {
                    "kind": "stat_mod",
                    "stat": "defense",
                    "amount": 2,
                    "duration_s": 120,
                },
            ],
        },
    },
    {
        "name": "Drowned Silver Ring",
        "rarity": "rare",
        "type": "wearable",
        "description": (
            "A flood-swollen silver band recovered below the tide line. Its "
            "scratched inner tally matches no name in the public archive."
        ),
        "art": {
            "kind": "url",
            "value": "/art/items/drazna/drowned-silver-ring-icon-v1.webp",
        },
        "payload": {
            "effects": [
                {"kind": "stat_mod", "stat": "max_hp", "amount": 6},
                {"kind": "stat_mod", "stat": "defense", "amount": 2},
            ],
        },
    },
)


REGIONAL_ITEMS_BY_REGION: dict[str, tuple[dict, ...]] = {
    "drazna": DRAZNA_ITEMS,
}

# Authored room ids already have stable kingdom prefixes. Generated frontier
# rooms deliberately have no match and therefore retain the unscoped pool.
REGION_CONTENT_PREFIXES: dict[str, str] = {
    "drazna": "drazna_",
}


def region_for_room_content_id(content_id: str | None) -> str | None:
    """Return the authored loot region for a stable room content id."""
    if not isinstance(content_id, str):
        return None
    return next(
        (
            region_id
            for region_id, prefix in REGION_CONTENT_PREFIXES.items()
            if content_id.startswith(prefix)
        ),
        None,
    )


def regional_art_values(region_id: str | None = None) -> frozenset[str]:
    """Stable art markers for one region, or for every regional item."""
    groups = (
        (REGIONAL_ITEMS_BY_REGION.get(region_id, ()),)
        if region_id is not None
        else REGIONAL_ITEMS_BY_REGION.values()
    )
    return frozenset(
        item["art"]["value"]
        for group in groups
        for item in group
    )
