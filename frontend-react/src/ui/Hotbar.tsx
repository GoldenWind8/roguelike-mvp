/**
 * The belt: ten fixed slots docked at the bottom of the screen, Minecraft-
 * style. Design-ahead for the intended action model — everything you do to
 * the world starts by holding an item (1–0 or click), then clicking a
 * target. Mock-only until inventory lands server-side.
 */
import { useGame, useGameApi } from "../store/gameStore";

export function Hotbar() {
  const { slots, selectedSlot } = useGame();
  const api = useGameApi();

  const held = selectedSlot !== null ? slots[selectedSlot] : null;

  return (
    <div className="hotbar-wrap">
      <div className={`hotbar-hint ${held ? "visible" : ""}`}>
        {held ? `Holding the ${held.name} — ${held.hint}. (Esc to put it away)` : " "}
      </div>
      <div className="hotbar">
        {slots.map((item, i) => (
          <button
            key={i}
            className={`slot ${item ? "" : "slot-empty"} ${i === selectedSlot ? "slot-held" : ""}`}
            title={item ? `${item.name} — ${item.description}` : "An empty loop on your belt."}
            onClick={() => api.selectSlot(i)}
          >
            <span className="slot-key">{(i + 1) % 10}</span>
            {item && (
              <>
                <span className="slot-icon">{item.icon}</span>
                {item.count > 1 && <span className="slot-count">{item.count}</span>}
              </>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
