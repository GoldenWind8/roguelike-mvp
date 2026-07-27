"""Authored shop catalogue.

Room objects opt into the generic ``shop`` interaction, while this catalogue
binds one stable placed-object id to its stock policy. The separation lets two
buildings with the same artwork carry different inventories later.
"""
from dataclasses import dataclass

from backend.content import load_catalog


@dataclass(frozen=True)
class ShopDefinition:
    id: str
    object_id: str
    label: str
    stock_size: int
    rarity_weights: dict[str, int]
    room_content_id: str | None = None
    buys_items: bool = False


def _definition(entry: dict) -> ShopDefinition:
    stock_size = entry.get("stock_size")
    weights = entry.get("rarity_weights")
    if not isinstance(stock_size, int) or not 1 <= stock_size <= 12:
        raise RuntimeError(f"shop {entry.get('id')!r} needs stock_size 1..12")
    if (
        not isinstance(weights, dict)
        or set(weights) != {"common", "rare", "legendary"}
        or any(not isinstance(value, int) or value < 0 for value in weights.values())
        or sum(weights.values()) <= 0
    ):
        raise RuntimeError(f"shop {entry.get('id')!r} has invalid rarity_weights")
    object_id = entry.get("object_id")
    label = entry.get("label")
    if not isinstance(object_id, str) or not object_id:
        raise RuntimeError(f"shop {entry.get('id')!r} needs an object_id")
    if not isinstance(label, str) or not label.strip():
        raise RuntimeError(f"shop {entry.get('id')!r} needs a label")
    room_content_id = entry.get("room_content_id")
    if room_content_id is not None and (
        not isinstance(room_content_id, str) or not room_content_id.strip()
    ):
        raise RuntimeError(
            f"shop {entry.get('id')!r} has invalid room_content_id"
        )
    buys_items = entry.get("buys_items", False)
    if not isinstance(buys_items, bool):
        raise RuntimeError(
            f"shop {entry.get('id')!r} has invalid buys_items policy"
        )
    return ShopDefinition(
        id=entry["id"],
        object_id=object_id,
        label=label.strip(),
        stock_size=stock_size,
        rarity_weights=dict(weights),
        room_content_id=(
            room_content_id.strip()
            if isinstance(room_content_id, str)
            else None
        ),
        buys_items=buys_items,
    )


_BY_ID = {
    shop_id: _definition(entry)
    for shop_id, entry in load_catalog("shops.json").items()
}
_BY_OBJECT = {definition.object_id: definition for definition in _BY_ID.values()}
if len(_BY_OBJECT) != len(_BY_ID):
    raise RuntimeError("shops.json repeats an object_id")


def get_shop(shop_id: str) -> ShopDefinition | None:
    return _BY_ID.get(shop_id)


def get_shop_for_object(object_id: str) -> ShopDefinition | None:
    return _BY_OBJECT.get(object_id)
