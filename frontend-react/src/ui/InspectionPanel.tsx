/** Server-answered close look at an object; opens only when there is one.
 * Opened chests show what waits inside (finds nobody chose to take —
 * docs/LOOT.md); "Look inside" is the same open_chest request, which the
 * server answers with chest_contents so the selection popup can rise. */
import { useEffect, useRef } from "react";

import { useGame, useGameApi } from "../store/gameStore";
import { ItemArt } from "./ItemArt";

export function InspectionPanel() {
  const { inspection, chestOpenPending } = useGame();
  const api = useGameApi();
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!inspection || !window.matchMedia("(max-width: 700px)").matches) return;
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    const frame = window.requestAnimationFrame(() => {
      panelRef.current?.focus({ preventScroll: true });
      panelRef.current?.scrollIntoView({
        behavior: reducedMotion ? "auto" : "smooth",
        block: "start",
      });
    });
    return () => {
      window.cancelAnimationFrame(frame);
      if (!previousFocus?.isConnected) return;
      previousFocus.focus({ preventScroll: true });
      previousFocus.scrollIntoView({
        behavior: reducedMotion ? "auto" : "smooth",
        block: "center",
      });
    };
  }, [inspection?.id]);

  if (!inspection) return null;

  const contents = inspection.contents ?? [];

  return (
    <section
      ref={panelRef}
      className="panel panel-inspection"
      tabIndex={-1}
      role="region"
      aria-live="polite"
      aria-labelledby="inspection-title"
    >
      <h3 id="inspection-title">
        A Closer Look
        <button
          className="panel-close"
          onClick={() => api.closeInspection()}
          aria-label="Close inspection"
        >
          ×
        </button>
      </h3>
      <div className="inspection-title">{inspection.label}</div>
      <p className="inspection-desc">{inspection.description}</p>
      <ul className="inspection-details">
        {inspection.details.map((d, i) => (
          <li key={i}>{d}</li>
        ))}
      </ul>

      {contents.length > 0 && (
        <div className="inspection-contents">
          <div className="contents-title">Waiting inside:</div>
          {contents.map((item, i) => (
            <div key={i} className={`contents-row rarity-${item.rarity}`} title={item.description}>
              <span className="contents-item">
                <ItemArt item={item} className="contents-item-icon" />
                <span>{item.name}</span>
              </span>
              <em>{item.rarity}</em>
            </div>
          ))}
          <button
            className="contents-take"
            disabled={chestOpenPending === inspection.id}
            onClick={() => {
              api.openChest(inspection.id);
              api.closeInspection();
            }}
          >
            {chestOpenPending === inspection.id
              ? "Opening…"
              : "Look inside (stand beside it)"}
          </button>
        </div>
      )}
    </section>
  );
}
