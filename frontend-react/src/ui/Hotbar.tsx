/**
 * The belt: ten fixed slots docked at the bottom of the screen, mirroring the
 * server's 10-slot pack (backend/inventory.py) straight off your player in
 * every broadcast. Left-click (or 1–0) holds an item; right-click (or R)
 * equips/unequips gear in place — equipped gear stays in its slot with a
 * highlight, per the design.
 */
import { packOf, SLOT_COUNT, useGame, useGameApi } from "../store/gameStore";
import type { InventorySlot } from "../net/types";
import { ItemArt } from "./ItemArt";

function hintFor(slot: InventorySlot): string {
  switch (slot.item.type) {
    case "consumable":
      return "click yourself to use it";
    case "throwable":
      return `click a tile within ${slot.item.payload.throw_range ?? 1} to throw it`;
    case "weapon":
    case "wearable":
      return slot.equipped ? "right-click (or R) to stow it" : "right-click (or R) to equip it";
  }
}

function titleFor(slot: InventorySlot): string {
  const it = slot.item;
  const bits = [`${it.name} (${it.rarity})`, it.description];
  if (it.type === "weapon") bits.push(`Damage ${it.payload.damage}, reach ${it.payload.range}.`);
  return bits.join(" — ");
}

export function Hotbar() {
  const { room, playerId, selectedSlot } = useGame();
  const api = useGameApi();

  const pack = packOf(room, playerId);
  const held = selectedSlot !== null ? pack[selectedSlot] : null;

  return (
    <div className="hotbar-wrap">
      <div className={`hotbar-hint ${held ? "visible" : ""}`}>
        {held ? `Holding the ${held.item.name} — ${hintFor(held)}. (Esc to put it away)` : " "}
      </div>
      <div className="hotbar">
        {Array.from({ length: SLOT_COUNT }, (_, i) => {
          const slot = pack[i] ?? null;
          const classes = ["slot"];
          if (!slot) classes.push("slot-empty");
          if (i === selectedSlot) classes.push("slot-held");
          if (slot?.equipped) classes.push("slot-equipped");
          if (slot) classes.push(`slot-${slot.item.rarity}`);
          return (
            <button
              key={i}
              className={classes.join(" ")}
              title={slot ? titleFor(slot) : "An empty loop on your belt."}
              onClick={() => api.selectSlot(i)}
              onContextMenu={(e) => {
                e.preventDefault();
                if (slot) api.toggleEquip(i);
              }}
            >
              <span className="slot-key">{(i + 1) % 10}</span>
              {slot && (
                <>
                  <ItemArt item={slot.item} className="slot-icon" />
                  {slot.quantity > 1 && <span className="slot-count">{slot.quantity}</span>}
                  {slot.equipped && <span className="slot-worn" title="Equipped">✦</span>}
                </>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
