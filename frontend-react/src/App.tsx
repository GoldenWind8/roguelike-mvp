import { useEffect, useMemo } from "react";
import { useGame, useGameApi } from "./store/gameStore";
import {
  RECENTER_ROOM_EVENT,
  RoomGrid,
  TOGGLE_FOOTPRINTS_EVENT,
} from "./grid/RoomGrid";
import type { ObjectSummary } from "./net/types";
import { StatusBar } from "./ui/StatusBar";
import { PartyPanel } from "./ui/PartyPanel";
import { PlayerPanel } from "./ui/PlayerPanel";
import { Hotbar } from "./ui/Hotbar";
import { InspectionPanel } from "./ui/InspectionPanel";
import { ChestLootModal } from "./ui/ChestLootModal";
import { EventLog } from "./ui/EventLog";
import { DialoguePanel } from "./ui/DialoguePanel";
import { LoginScreen } from "./ui/LoginScreen";
import { ShopModal } from "./ui/ShopModal";
import { NoticeboardModal } from "./ui/NoticeboardModal";
import { WorldDrawer } from "./ui/WorldDrawer";
import { CarriageModal } from "./ui/CarriageModal";
import { DiscoveryToast } from "./ui/DiscoveryToast";

const manhattan = (a: [number, number], b: [number, number]) =>
  Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);

const distanceToObject = (position: [number, number], object: ObjectSummary) =>
  Math.min(...(object.occupied_cells.length > 0 ? object.occupied_cells : [object.position])
    .map((cell) => manhattan(position, cell)));

const LOCAL_FOOTPRINT_DEBUG = ["localhost", "127.0.0.1"].includes(window.location.hostname);

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

      if (e.key.toLowerCase() === "j") {
        e.preventDefault();
        if (state.worldDrawerOpen) api.closeWorldDrawer();
        else if (!state.lootReveal && !state.shop && !state.noticeboard && !state.carriage) api.openWorldDrawer();
        return;
      }

      // The World is a reading surface, not a pause menu. It blocks local
      // controls while open, but the server-side world remains authoritative.
      if (state.worldDrawerOpen) {
        if (e.key === "Escape") {
          e.preventDefault();
          api.closeWorldDrawer();
        }
        return;
      }

      // The counter is a modal interaction: no walking away while a purchase
      // is being considered. The server still revalidates adjacency on Buy.
      if (state.shop || state.noticeboard || state.carriage) {
        if (e.key === "Escape") {
          e.preventDefault();
          if (state.shop) api.closeShop();
          else if (state.noticeboard) api.closeNoticeboard();
          else api.closeCarriage();
        }
        return;
      }

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
      } else if (e.key === " ") {
        e.preventDefault();
        api.wait();
      } else if (/^[0-9]$/.test(e.key)) {
        // 1–9 are slots 0–8; 0 is the tenth slot, Minecraft-style.
        api.selectSlot(e.key === "0" ? 9 : Number(e.key) - 1);
      } else if (e.key.toLowerCase() === "e") {
        e.preventDefault();
        if (state.dialogue || state.lootReveal || state.shop || state.noticeboard || state.carriage) return;
        const room = state.room;
        const me = room && state.playerId ? room.players[state.playerId] : null;
        if (!room || !me) return;

        // People win ties over scenery, then stable names/ids keep the choice
        // predictable when several things are beside the player.
        const npc = Object.values(room.npcs)
          .filter((candidate) => candidate.is_alive
            && candidate.disposition !== "hostile"
            && manhattan(me.position, candidate.position) === 1)
          .sort((a, b) => a.name.localeCompare(b.name))[0];
        if (npc) {
          api.openDialogue(npc);
          return;
        }

        const object = room.objects
          .filter((candidate) => distanceToObject(me.position, candidate) <= 1)
          .sort((a, b) => a.label.localeCompare(b.label))[0];
        if (object) {
          if (object.type === "chest") api.openChest(object.id);
          else if (object.interaction === "shop") api.openShop(object.id);
          else if (object.interaction === "noticeboard") api.openNoticeboard(object.id);
          else if (object.interaction === "carriage") api.openCarriage(object.id);
          else api.inspect(object.id);
          return;
        }
        api.note("ambient", "Nothing nearby asks for your attention.");
      } else if (e.key.toLowerCase() === "r") {
        // Equip/stow the held slot (gear only; the api explains otherwise).
        if (state.selectedSlot !== null) api.toggleEquip(state.selectedSlot);
      } else if (e.key === "Home") {
        e.preventDefault();
        window.dispatchEvent(new Event(RECENTER_ROOM_EVENT));
      } else if (e.key === "F2" && LOCAL_FOOTPRINT_DEBUG) {
        e.preventDefault();
        window.dispatchEvent(new Event(TOGGLE_FOOTPRINTS_EVENT));
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
  }, [api, state.screen, state.room, state.playerId, state.selectedSlot, state.dialogue, state.inspection, state.lootReveal, state.shop, state.noticeboard, state.carriage, state.worldDrawerOpen]);

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
      <ShopModal />
      <NoticeboardModal />
      <CarriageModal />
      <WorldDrawer />
      <DiscoveryToast />

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
