import type { ItemView } from "../net/types";

interface ItemArtProps {
  item: Pick<ItemView, "art" | "name">;
  className?: string;
}

/** Render typed item art without ever treating an image URL as visible text. */
export function ItemArt({ item, className = "" }: ItemArtProps) {
  return (
    <span className={`${className} item-art`.trim()} aria-hidden>
      {item.art.kind === "url" ? (
        <img
          className="item-art-image"
          src={item.art.value}
          alt=""
          draggable={false}
        />
      ) : (
        item.art.value
      )}
    </span>
  );
}

/** Plain-text narration may keep emoji, but must never print asset URLs. */
export function itemLogLabel(item: Pick<ItemView, "art" | "name">): string {
  return item.art.kind === "emoji"
    ? `${item.art.value} ${item.name}`
    : item.name;
}
