/** Here & Now: transient, observable action in this room, newest at bottom. */
import { useEffect, useRef } from "react";
import { useGame } from "../store/gameStore";

export function EventLog() {
  const { log } = useGame();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);

  return (
    <section className="panel panel-chronicle">
      <h3>
        <span>Here &amp; Now</span>
        <small>this room</small>
      </h3>
      <div className="chronicle" ref={scrollRef}>
        {log.length === 0 && (
          <div className="chronicle-empty">
            <span aria-hidden>·</span>
            <strong>The room holds its breath.</strong>
            <em>Nearby actions will leave their trace here.</em>
          </div>
        )}
        {log.map((line) => (
          <div key={line.id} className={`chron-line chron-${line.kind}`}>
            {line.text}
          </div>
        ))}
      </div>
    </section>
  );
}
