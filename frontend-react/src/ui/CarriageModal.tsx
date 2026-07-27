import { useEffect, useState } from "react";
import type { CarriageDestinationView } from "../net/types";
import { useGame, useGameApi } from "../store/gameStore";

function travelTime(minutes: number | undefined): string | null {
  if (!minutes || minutes <= 0) return null;
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours}h ${rest}m` : `${hours}h`;
}

function Destination({
  destination,
  pending,
  coins,
  onTravel,
}: {
  destination: CarriageDestinationView;
  pending: boolean;
  coins: number;
  onTravel: () => void;
}) {
  const time = travelTime(destination.travel_minutes);
  const fare = destination.fare;
  const canAfford = coins >= fare;
  const layovers = Math.max(0, destination.route_stop_ids.length - 2);
  return (
    <article className="carriage-route">
      <span className="carriage-route-mark" aria-hidden>◆</span>
      <div className="carriage-route-copy">
        <h3>{destination.name}</h3>
        <div className="carriage-route-facts">
          <span>{layovers === 0 ? "Direct road" : `${layovers} change${layovers === 1 ? "" : "s"}`}</span>
        </div>
      </div>
      <div className="carriage-route-action">
        {time && <span>{time}</span>}
        {fare > 0 && <span>{fare} coin{fare === 1 ? "" : "s"}</span>}
        <button
          disabled={pending || !canAfford}
          onClick={onTravel}
        >
          {!canAfford ? "Fare too high" : "Travel"}
        </button>
      </div>
    </article>
  );
}

export function CarriageModal() {
  const { carriage, carriagePending, room, playerId } = useGame();
  const api = useGameApi();
  const [name, setName] = useState("");

  useEffect(() => {
    setName("");
  }, [carriage?.stop.id]);

  if (!carriage) return null;

  const stopName = carriage.stop.name;
  const trimmed = name.trim();
  const coins = playerId && room ? room.players[playerId]?.coins ?? 0 : 0;

  return (
    <div
      className="carriage-veil"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && !carriagePending) api.closeCarriage();
      }}
    >
      <section
        className="carriage-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="carriage-title"
      >
        <header className="carriage-head">
          <div className="carriage-glyph" aria-hidden>
            <span>✦</span>
          </div>
          <div>
            <span className="carriage-kicker">Shared road network</span>
            <h2 id="carriage-title">{stopName}</h2>
            <p>{carriage.stop.biome.replace(/[_-]+/g, " ")}</p>
          </div>
          <button
            className="carriage-close"
            onClick={() => api.closeCarriage()}
            disabled={carriagePending === "travel"}
            title="Step away (Esc)"
            aria-label="Close carriage routes"
          >
            ×
          </button>
        </header>

        {carriage.can_name && (
          <form
            className="carriage-naming"
            onSubmit={(event) => {
              event.preventDefault();
              api.nameCarriageStop(trimmed);
            }}
          >
            <div>
              <label htmlFor="carriage-stop-name">Leave this stop a name</label>
              <p>Other travellers will know it by the same name.</p>
            </div>
            <div className="carriage-name-row">
              <input
                id="carriage-stop-name"
                value={name}
                maxLength={carriage.name_limit}
                disabled={carriagePending !== null}
                placeholder="Carve a name into the post…"
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") api.closeCarriage();
                }}
              />
              <button
                type="submit"
                disabled={!trimmed || carriagePending !== null}
              >
                {carriagePending === "name" ? "Carving…" : "Name it"}
              </button>
            </div>
            <span className="carriage-name-count">{name.length}/{carriage.name_limit}</span>
          </form>
        )}

        {!carriage.can_name && carriage.stop.community_named && (
          <p className="carriage-named-by">
            {carriage.stop.named_by
              ? `The sign was named by ${carriage.stop.named_by}.`
              : "Another traveller left this stop its name."}
          </p>
        )}

        <div className="carriage-routes-head">
          <h3>Known roads from here</h3>
          <span>Only publicly named stops appear.</span>
        </div>

        <div className="carriage-routes">
          {carriage.destinations.length === 0 ? (
            <div className="carriage-empty">
              <span aria-hidden>⌁</span>
              <strong>No road answers from here yet.</strong>
              <p>Find another carriage post in the world to bind it to this route.</p>
            </div>
          ) : (
            carriage.destinations.map((destination) => (
              <Destination
                key={destination.stop_id}
                destination={destination}
                pending={carriagePending !== null}
                coins={coins}
                onTravel={() => api.travelByCarriage(destination.stop_id)}
              />
            ))
          )}
        </div>

        <footer className="carriage-foot">
          <span>Carriages move through world time. People may not wait for you.</span>
          {carriagePending === "travel" && <strong>The driver readies the horses…</strong>}
        </footer>
      </section>
    </div>
  );
}
