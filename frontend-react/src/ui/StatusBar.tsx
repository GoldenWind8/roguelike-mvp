/**
 * The top strip: where you are and the room's live mode, with music, help,
 * connection, and logout at the edge. Personal vitals live in the left
 * "You" panel so this stays a place strip, not a character sheet.
 */
import { useState } from "react";
import { useGame, useGameApi } from "../store/gameStore";

const HELP_LINES: [string, string][] = [
  ["Arrows / WASD", "wander the room"],
  ["1–0 or click the belt", "hold an item"],
  ["Item, then target", "how everything is done — sword on an enemy, bomb on a tile, food on yourself"],
  ["Click a person", "talk (walk up close first)"],
  ["Click an object", "take a closer look"],
  ["Esc", "close the chat, put the item away"],
  ["♪", "hush or wake the music"],
];

export function StatusBar() {
  const { room, connection, musicOn } = useGame();
  const api = useGameApi();
  const [helpOpen, setHelpOpen] = useState(false);

  const mode = room?.room.mode ?? "exploration";

  return (
    <header className="status-bar">
      <div className="brand">
        <span className="brand-flame">🕯️</span>
        <span className="brand-name">Emberhollow</span>
      </div>

      <div className="room-title">
        <span className="room-name">{room?.room.name ?? "…"}</span>
        <span className={`mode-chip mode-${mode}`}>
          {mode === "combat" ? "⚔️ Danger" : "🕯️ Hearthside"}
        </span>
      </div>

      <div className="status-right">
        <button
          className="icon-btn"
          onClick={() => api.toggleMusic()}
          title={musicOn ? "Hush the music" : "Let the music play"}
        >
          {musicOn ? "♪" : "𝄽"}
        </button>
        <div className="help-anchor">
          <button
            className={`icon-btn ${helpOpen ? "icon-btn-active" : ""}`}
            onClick={() => setHelpOpen((v) => !v)}
            title="How to be here"
          >
            ?
          </button>
          {helpOpen && (
            <div className="help-pop">
              <h4>How to be here</h4>
              {HELP_LINES.map(([keys, what]) => (
                <div key={keys} className="help-row">
                  <span className="help-keys">{keys}</span>
                  <span className="help-what">{what}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <span className={`conn-dot conn-${connection}`} title={connection} />
        <button className="icon-btn leave-btn" onClick={() => api.logout()} title="Leave the hall (log out)">
          ⏻
        </button>
      </div>
    </header>
  );
}
