"""Premium-LLM item invention (docs/LOOT.md Decision 2): called by
loot.spawn_loot at chest-open time, small-chance, always fallback-guarded.

Trust boundary, same as dialogue effects and AI room placement: the model
PROPOSES a JSON item; items.clamp_item pulls its numbers into the rarity
band; items.validate_item (via insert_item) accepts or rejects the shape.
Only then does it become a pool row — origin="llm" — permanently part of the
game's item universe. AI proposes, the engine disposes.

Art: the model picks an EMOJI (items.ART_KINDS today). When the image-gen
model arrives this module asks for/attaches {"kind": "url"} art instead —
the art field was designed as a typed reference exactly so that swap touches
nothing downstream.
"""
import json
import re

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import LOOT_LLM_MAX_TOKENS, LOOT_LLM_TIMEOUT
from backend.items import (
    AREA_SHAPES,
    MAX_AREA_SIZE,
    MAX_RANGE,
    RARITY_CAPS,
    Rarity,
    STATS,
    clamp_item,
)
from backend.item_store import insert_item
from backend.llm import complete_tier, strip_code_fence

GENERATION_TIER = "premium"


def _schema_block(rarity: str) -> str:
    caps = RARITY_CAPS[Rarity(rarity)]
    return (
        'Reply with ONE JSON object, no prose, exactly this shape:\n'
        '{\n'
        f'  "name": "<evocative, <= 40 chars>",\n'
        f'  "description": "<one flavorful sentence, <= 200 chars>",\n'
        f'  "rarity": "{rarity}",\n'
        '  "type": "<wearable | consumable | throwable | weapon>",\n'
        '  "art": {"kind": "emoji", "value": "<ONE fitting emoji>"},\n'
        '  "payload": <see rules for the type you chose>\n'
        '}\n\n'
        'Payload rules by type (all numbers are integers):\n'
        f'- wearable: {{"effects": [{{"kind": "stat_mod", "stat": <one of {list(STATS)}>, '
        '"amount": <int, may be negative for cursed gear>}}, ...]}\n'
        f'- consumable: {{"effects": [{{"kind": "restore_hp", "amount": <1..{caps["heal"]}>}} and/or '
        f'{{"kind": "restore_hunger", "amount": <1..{caps["hunger"]}> — food value, for edible items}} and/or '
        f'{{"kind": "stat_mod", "stat": ..., "amount": ..., "duration_s": <seconds, <= {caps["duration_s"]}>}}]}}\n'
        f'- throwable: {{"throw_range": <1..{MAX_RANGE}>, "area": {{"shape": "{AREA_SHAPES[0]}", '
        f'"size": <0..{MAX_AREA_SIZE}>}}, "effects": [{{"kind": "damage", "amount": <1..{caps["throw_damage"]}>}} '
        'and/or timed stat_mod atoms]}\n'
        f'- weapon: {{"damage": <1..{caps["weapon_damage"]}>, "range": <1 melee .. {MAX_RANGE} ranged>}}\n\n'
        f'Power budget for {rarity}: total |stat_mod amounts| <= {caps["stat_total"]}.\n'
        'Combat scale for reference: players hit for ~30 and have 100 hp; enemies have 4-8 hp.'
    )


def _prompt(rarity: str) -> list[dict]:
    return [
        {"role": "system", "content": (
            "You invent loot for a grim, candlelit multiplayer roguelike. "
            "Items are darkly whimsical, never referencing the real world. "
            "You reply with a single JSON object and nothing else."
        )},
        {"role": "user", "content": (
            f"Invent one {rarity} item nobody has ever seen.\n\n" + _schema_block(rarity)
        )},
    ]


def _lenient_json(content: str) -> dict:
    """Strict json.loads, then one retry with the single most common provider
    quirk repaired (trailing commas before } or ]). Anything still broken
    raises — magnitude is clamped, shape is never guessed at."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", content))


async def generate_item(session: AsyncSession, rarity: str) -> dict:
    """One minted item_view, inserted into the pool — or an exception (the
    caller's fallback path owns what failure means; here everything raises)."""
    content = await complete_tier(
        GENERATION_TIER, _prompt(rarity),
        max_tokens=LOOT_LLM_MAX_TOKENS, timeout=LOOT_LLM_TIMEOUT,
    )
    data = _lenient_json(strip_code_fence(content))
    # The roll decided the rarity; the model doesn't get to upgrade itself.
    data["rarity"] = rarity
    item = await insert_item(session, clamp_item(data), origin="llm")
    await session.commit()
    return item
