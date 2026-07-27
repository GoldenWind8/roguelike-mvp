import { useEffect } from "react";
import { useGame, useGameApi } from "../store/gameStore";

function depthLabel(depth: number): string {
  if (depth <= 0) return "At the settled edge";
  if (depth === 1) return "Beyond the last signpost";
  if (depth === 2) return "In the outer wilds";
  return "Far from the settled road";
}

function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function DiscoveryToast() {
  const { discoveryToast } = useGame();
  const api = useGameApi();

  useEffect(() => {
    if (!discoveryToast) return;
    const id = discoveryToast.id;
    const timer = window.setTimeout(() => api.dismissDiscovery(id), 6500);
    return () => window.clearTimeout(timer);
  }, [api, discoveryToast]);

  if (!discoveryToast) return null;

  return (
    <aside className="discovery-toast" role="status" aria-live="polite">
      <span className="discovery-sigil" aria-hidden>✦</span>
      <div>
        <span className="discovery-kicker">A place takes shape</span>
        <strong>{discoveryToast.name}</strong>
        <p>
          {titleCase(discoveryToast.biome)}
          <span aria-hidden> · </span>
          {depthLabel(discoveryToast.depth)}
        </p>
        <em>{discoveryToast.majorRegion}</em>
      </div>
      <button
        onClick={() => api.dismissDiscovery(discoveryToast.id)}
        aria-label="Dismiss discovery"
      >
        ×
      </button>
    </aside>
  );
}
