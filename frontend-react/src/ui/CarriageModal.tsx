import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import type { CarriageDestinationView } from "../net/types";
import { useGame, useGameApi } from "../store/gameStore";
import { usePointerSettle } from "./usePointerSettle";

function travelTime(minutes: number | undefined): string | null {
  if (!minutes || minutes <= 0) return null;
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours}h ${rest}m` : `${hours}h`;
}

function clockTime(minuteOfDay: number | null): string | null {
  if (minuteOfDay === null) return null;
  const hour = Math.floor(minuteOfDay / 60) % 24;
  const minute = minuteOfDay % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function dangerLabel(danger: number): string {
  if (danger <= 0) return "Watched road";
  if (danger === 1) return "Uneasy road";
  if (danger === 2) return "Hazardous road";
  return "Dire road";
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
  onTravel: (event: MouseEvent<HTMLButtonElement>) => void;
}) {
  const roadTime = travelTime(destination.travel_minutes);
  const journeyTime = travelTime(destination.journey_minutes);
  const fare = destination.fare;
  const canAfford = coins >= fare;
  const layovers = Math.max(0, destination.route_stop_ids.length - 2);
  const departure = clockTime(destination.next_departure_minute_of_day);
  const boardsNow = destination.available_now;
  const schedule = destination.next_departure_minute === null
    ? "On demand"
    : boardsNow
      ? `Boarding now${departure ? ` · ${departure}` : ""}`
      : `Next ${departure ?? "service"} · in ${travelTime(destination.wait_minutes) ?? "moments"}`;
  const status = destination.route_status === "operating"
    ? dangerLabel(destination.max_leg_danger)
    : destination.route_status.replace(/[_-]+/g, " ");
  return (
    <article className={`carriage-route${boardsNow ? "" : " carriage-route-closed"}`}>
      <span className="carriage-route-mark" aria-hidden>◆</span>
      <div className="carriage-route-copy">
        <h3>{destination.name}</h3>
        <div className="carriage-route-facts">
          <span>{layovers === 0 ? "Direct road" : `${layovers} change${layovers === 1 ? "" : "s"}`}</span>
          <span>{schedule}</span>
          <span>{status}</span>
        </div>
        {destination.transfer_wait_minutes > 0 && (
          <em>{travelTime(destination.transfer_wait_minutes)} between carriages</em>
        )}
      </div>
      <div className="carriage-route-action">
        {journeyTime && (
          <span title={roadTime ? `${roadTime} moving` : undefined}>
            {journeyTime}
          </span>
        )}
        {fare > 0 && <span>{fare} coin{fare === 1 ? "" : "s"}</span>}
        <button
          disabled={pending || !canAfford || !boardsNow}
          onClick={onTravel}
          aria-label={
            boardsNow && canAfford
              ? `Board for ${destination.name}${fare > 0 ? `, ${fare} coin${fare === 1 ? "" : "s"}` : ""}`
              : `${destination.name}: ${!canAfford ? "fare too high" : "not boarding now"}`
          }
        >
          {!canAfford ? "Fare too high" : !boardsNow ? "Not boarding" : "Board"}
        </button>
      </div>
    </article>
  );
}

export function CarriageModal() {
  const { carriage, carriagePending, room, playerId } = useGame();
  const api = useGameApi();
  const [name, setName] = useState("");
  const modalRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const pointerSettled = usePointerSettle(carriage?.stop.id);

  useEffect(() => {
    setName("");
  }, [carriage?.stop.id]);

  useEffect(() => {
    if (!carriage) return;
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const frame = window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [carriage?.stop.id]);

  useEffect(() => {
    if (!carriage) return;
    const frame = window.requestAnimationFrame(() => {
      const modal = modalRef.current;
      const active = document.activeElement;
      if (!modal) return;
      if (carriagePending === "travel") {
        modal.focus();
        return;
      }
      const activeControlIsDisabled = (
        active instanceof HTMLButtonElement
        || active instanceof HTMLInputElement
      ) && active.disabled;
      if (modal.contains(active) && !activeControlIsDisabled) return;
      (
        modal.querySelector<HTMLElement>(
          ".carriage-close:not(:disabled), button:not(:disabled), input:not(:disabled), [tabindex='0']",
        )
        ?? modal
      ).focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [
    carriage?.stop.id,
    carriage?.can_name,
    carriage?.destinations,
    carriagePending,
  ]);

  if (!carriage) return null;

  const stopName = carriage.stop.name;
  const trimmed = name.trim();
  const validName = trimmed.length >= 2;
  const coins = playerId && room ? room.players[playerId]?.coins ?? 0 : 0;
  const serviceClock = clockTime(carriage.service.minute_of_day);

  return (
    <div
      className="carriage-veil"
      onMouseDown={(event) => {
        if (
          event.currentTarget === event.target
          && !carriagePending
          && pointerSettled(event)
        ) {
          api.closeCarriage();
        }
      }}
    >
      <section
        ref={modalRef}
        className="carriage-modal"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="carriage-title"
        aria-busy={carriagePending !== null}
        onKeyDown={(event) => {
          if (event.key !== "Tab" || !modalRef.current) return;
          const focusable = Array.from(
            modalRef.current.querySelectorAll<HTMLElement>(
              "button:not(:disabled), input:not(:disabled), [href], [tabindex='0']",
            ),
          );
          if (focusable.length === 0) {
            event.preventDefault();
            modalRef.current.focus();
            return;
          }
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && (
            document.activeElement === first
            || document.activeElement === modalRef.current
          )) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }}
      >
        <header className="carriage-head">
          <div className="carriage-glyph" aria-hidden>
            <span>✦</span>
          </div>
          <div>
            <span className="carriage-kicker">Shared road network</span>
            <h2 id="carriage-title">{stopName}</h2>
            <p>
              {carriage.stop.biome.replace(/[_-]+/g, " ")}
              {serviceClock ? ` · ${serviceClock}` : ""}
              {` · ${carriage.service.status}`}
            </p>
          </div>
          <button
            ref={closeRef}
            className="carriage-close"
            onClick={(event) => {
              if (pointerSettled(event)) api.closeCarriage();
            }}
            disabled={carriagePending !== null}
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
              if (validName) api.nameCarriageStop(trimmed);
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
                minLength={2}
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
                disabled={!validName || carriagePending !== null}
                onClick={(event) => {
                  if (!pointerSettled(event)) event.preventDefault();
                }}
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
          <h3 id="carriage-routes-title">Known roads from here</h3>
          <span>Only publicly named stops appear.</span>
        </div>

        <div
          className="carriage-routes"
          role="region"
          aria-labelledby="carriage-routes-title"
          tabIndex={carriage.destinations.length > 0 ? 0 : -1}
        >
          {carriage.destinations.length === 0 ? (
            <div className="carriage-empty">
              <span aria-hidden>⌁</span>
              <strong>
                {carriage.stop.status === "closed"
                  ? "The service is closed."
                  : "No road answers from here yet."}
              </strong>
              <p>
                {carriage.stop.status === "closed"
                  ? "The carriage waits for the lost road to be found from the frontier."
                  : "Find another carriage post in the world to bind it to this route."}
              </p>
            </div>
          ) : (
            carriage.destinations.map((destination) => (
              <Destination
                key={destination.stop_id}
                destination={destination}
                pending={carriagePending !== null}
                coins={coins}
                onTravel={(event) => {
                  if (pointerSettled(event)) {
                    api.travelByCarriage(destination.stop_id);
                  }
                }}
              />
            ))
          )}
        </div>

        <footer className="carriage-foot">
          <span>Carriages move through world time. People may not wait for you.</span>
          {carriagePending === "travel" && (
            <strong role="status">The driver readies the horses…</strong>
          )}
        </footer>
      </section>
    </div>
  );
}
