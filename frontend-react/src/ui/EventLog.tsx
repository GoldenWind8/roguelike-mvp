/** The Chronicle: the room's running story, newest at the bottom. */
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
      <h3>The Chronicle</h3>
      <div className="chronicle" ref={scrollRef}>
        {log.map((line) => (
          <div key={line.id} className={`chron-line chron-${line.kind}`}>
            {line.text}
          </div>
        ))}
      </div>
    </section>
  );
}
