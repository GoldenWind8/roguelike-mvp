import { useEffect, useMemo } from "react";
import { useGame, useGameApi } from "./store/gameStore";
import { RoomGrid } from "./grid/RoomGrid";
import { StatusBar } from "./ui/StatusBar";
import { PartyPanel } from "./ui/PartyPanel";
import { PlayerPanel } from "./ui/PlayerPanel";
import { Hotbar } from "./ui/Hotbar";
import { InspectionPanel } from "./ui/InspectionPanel";
import { ChestLootModal } from "./ui/ChestLootModal";
import { EventLog } from "./ui/EventLog";
import { DialoguePanel } from "./ui/DialoguePanel";
import { LoginScreen } from "./ui/LoginScreen";

/** A drift of embers rising past the panels; pure decoration. */
function Embers() {
  const embers = useMemo(
    () =>
      Array.from({ length: 14 }, (_, i) => ({
        id: i,
        left: `${Math.random() * 100}%`,
        size: 2 + Math.random() * 3,
        duration: `${9 + Math.random() * 14}s`,
        delay: `${-Math.random() * 20}s`,
      })),
    [],
  );
  return (
    <div className="embers" aria-hidden>
      {embers.map((e) => (
        <span
          key={e.id}
          style={{
            left: e.left,
            width: e.size,
            height: e.size,
            animationDuration: e.duration,
            animationDelay: e.delay,
          }}
        />
      ))}
    </div>
  );
}

export default function App() {
  const state = useGame();
  const api = useGameApi();
  const mode = state.room?.room.mode ?? "exploration";
  const me = state.playerId && state.room ? state.room.players[state.playerId] : null;

  // A stored session skips the front door.
  useEffect(() => {
    api.resume();
  }, [api]);

  // Death recovery: the server respawns a dead character on the NEXT join —
  // so after the blackout beat, quietly disconnect and rejoin with the same
  // token.
  const alive = me?.is_alive;
  useEffect(() => {
    if (alive !== false) return;
    const timer = window.setTimeout(() => api.rejoin(), 4500);
    return () => window.clearTimeout(timer);
  }, [alive, api]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
      if (state.screen !== "game") return;

      const moves: Record<string, [number, number]> = {
        ArrowUp: [0, -1], w: [0, -1],
        ArrowDown: [0, 1], s: [0, 1],
        ArrowLeft: [-1, 0], a: [-1, 0],
        ArrowRight: [1, 0], d: [1, 0],
      };
      const dir = moves[e.key] ?? moves[e.key.toLowerCase()];
      if (dir) {
        e.preventDefault();
        api.move(dir[0], dir[1]);
      } else if (/^[0-9]$/.test(e.key)) {
        // 1–9 are slots 0–8; 0 is the tenth slot, Minecraft-style.
        api.selectSlot(e.key === "0" ? 9 : Number(e.key) - 1);
      } else if (e.key.toLowerCase() === "e") {
        // Equip/stow the held slot (gear only; the api explains otherwise).
        if (state.selectedSlot !== null) api.toggleEquip(state.selectedSlot);
      } else if (e.key === "Escape") {
        // The loot reveal sits on top of everything, so it closes first;
        // then chat — the thing you most often want out of the way.
        if (state.lootReveal) api.closeLoot();
        else if (state.dialogue) api.closeDialogue();
        else if (state.selectedSlot !== null) api.selectSlot(null);
        else if (state.inspection) api.closeInspection();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [api, state.screen, state.selectedSlot, state.dialogue, state.inspection, state.lootReveal]);

  if (state.screen === "login") {
    return (
      <div className="app" data-mode="exploration">
        <Embers />
        <div className="vignette" />
        <LoginScreen />
      </div>
    );
  }

  return (
    <div className="app" data-mode={mode}>
      <Embers />
      <div className="vignette" />
      <div className="vignette vignette-danger" />

      <StatusBar />

      <main className="layout">
        <aside className="sidebar sidebar-left">
          <PlayerPanel />
          <PartyPanel />
        </aside>
        <RoomGrid />
        <aside className="sidebar sidebar-right">
          {state.dialogue ? (
            <DialoguePanel />
          ) : (
            <>
              <InspectionPanel />
              <EventLog />
            </>
          )}
        </aside>
      </main>

      <Hotbar />
      <ChestLootModal />

      {me && !me.is_alive && (
        <div className="death-veil">
          <div>
            <div className="death-title">You black out…</div>
            <div className="death-sub">The fire crackles somewhere far away. Someone is dragging you.</div>
          </div>
        </div>
      )}
    </div>
  );
}
