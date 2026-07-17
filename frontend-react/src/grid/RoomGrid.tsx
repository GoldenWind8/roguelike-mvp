/**
 * The room itself (FRONTEND_DESIGN.md "grid/"): renders the server grid and
 * turns clicks into intents. All rules live server-side; the local knowledge
 * here is presentation (icons), the camera, and the held-item action model —
 * acting on the world means holding something from the belt first.
 *
 * Camera: rooms come in generated sizes, so the world pans beneath a fixed
 * frame — the tiles move, the border doesn't, nothing shows a scrollbar.
 * It auto-centres on you each time you move; drag to look around, and the
 * camera recentres on your next step.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useGame, useGameApi } from "../store/gameStore";
import type { ActorState, NpcState, ObjectSummary } from "../net/types";

type ActorKind = "player" | "enemy" | "npc";

/** Presentation for an NPC by their role text; shared with the dialogue head. */
export function npcIcon(role: string): string {
  const r = role.toLowerCase();
  if (r.includes("heal") || r.includes("sellsword")) return "🧝‍♀️";
  if (r.includes("caretaker")) return "🧓";
  if (r.includes("innkeep")) return "🧔";
  return "🧑";
}

const ENEMY_ICONS: [string, string][] = [
  ["rat", "🐀"],
  ["goblin", "👺"],
  ["skeleton", "💀"],
  ["bunny", "🐇"],
];

function actorIcon(kind: ActorKind, actor: ActorState, isMe: boolean): string {
  if (kind === "player") return isMe ? "🧝" : "🧙";
  if (kind === "enemy") {
    const name = actor.name.toLowerCase();
    return ENEMY_ICONS.find(([k]) => name.includes(k))?.[1] ?? "👹";
  }
  return npcIcon((actor as NpcState).role);
}

const OBJECT_ICONS: Record<string, string> = {
  hearth: "🔥",
  notice_board: "📜",
  crate: "📦",
  cellar_door: "🕳️",
  door: "🚪",
  chest: "🧰",
  fire_barrel: "🛢️",
};

// The server's targeting rule for attack AND talk: orthogonally adjacent
// (Manhattan distance 1) — diagonals don't count. Mirrored here only to give
// a friendly hint instead of a server error.
const manhattan = (a: [number, number], b: [number, number]) =>
  Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);

function HpBar({ actor }: { actor: ActorState }) {
  const pct = Math.max(0, Math.round((actor.hp / actor.max_hp) * 100));
  const tone = pct > 60 ? "high" : pct > 30 ? "mid" : "low";
  return (
    <div className="hp-bar">
      <div className={`hp-fill hp-${tone}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

interface Camera {
  x: number;
  y: number;
  animate: boolean;
  /** Which frame edges have more room beyond them (fade those into shadow;
   * a crisp rim on the others means "that's the actual wall"). */
  fades: { left: boolean; right: boolean; top: boolean; bottom: boolean };
}

const NO_FADES = { left: false, right: false, top: false, bottom: false };

export function RoomGrid() {
  const { room, playerId, slots, selectedSlot, actionLocked, waitingFor } = useGame();
  const api = useGameApi();
  const frameRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const [cam, setCam] = useState<Camera>({ x: 0, y: 0, animate: false, fades: NO_FADES });
  const dragRef = useRef<{ sx: number; sy: number; cx: number; cy: number } | null>(null);
  const suppressClickRef = useRef(false);

  const held = selectedSlot !== null ? slots[selectedSlot] : null;
  const me = room && playerId ? room.players[playerId] : null;

  const walls = useMemo(() => {
    const set = new Set<string>();
    room?.walls.forEach(([x, y]) => set.add(`${x},${y}`));
    return set;
  }, [room?.walls]);

  const objectsAt = useMemo(() => {
    const map = new Map<string, ObjectSummary>();
    room?.objects.forEach((o) => map.set(`${o.position[0]},${o.position[1]}`, o));
    return map;
  }, [room?.objects]);

  // Clamp a camera offset so the frame never shows past the room's edge
  // (rooms smaller than the frame sit centred), and note which edges still
  // have room continuing beyond them.
  const clampCam = useCallback(
    (x: number, y: number): { x: number; y: number; fades: Camera["fades"] } => {
      const frame = frameRef.current;
      const grid = gridRef.current;
      if (!frame || !grid) return { x, y, fades: NO_FADES };
      const fit = (target: number, gridSize: number, frameSize: number) =>
        gridSize <= frameSize
          ? (gridSize - frameSize) / 2
          : Math.min(Math.max(target, 0), gridSize - frameSize);
      const cx = fit(x, grid.offsetWidth, frame.clientWidth);
      const cy = fit(y, grid.offsetHeight, frame.clientHeight);
      return {
        x: cx,
        y: cy,
        fades: {
          left: cx > 2,
          right: cx < grid.offsetWidth - frame.clientWidth - 2,
          top: cy > 2,
          bottom: cy < grid.offsetHeight - frame.clientHeight - 2,
        },
      };
    },
    [],
  );

  // The camera: keep me centred whenever I move (or revive elsewhere).
  const meX = me?.position[0] ?? 0;
  const meY = me?.position[1] ?? 0;
  useEffect(() => {
    const frame = frameRef.current;
    const grid = gridRef.current;
    if (!frame || !grid) return;
    const cell = grid.querySelector<HTMLElement>(".cell");
    if (!cell) return;
    const stride = cell.offsetWidth + 3; // cell + grid gap
    const pad = 10; // grid padding
    const target = clampCam(
      pad + meX * stride + stride / 2 - frame.clientWidth / 2,
      pad + meY * stride + stride / 2 - frame.clientHeight / 2,
    );
    setCam({ ...target, animate: true });
  }, [meX, meY, clampCam]);

  // Drag to look around; a real drag swallows the click that would follow.
  // Pointer capture starts only once the drag threshold is crossed — capturing
  // on pointerdown would retarget the click away from the tile under it.
  const onPointerDown = (e: React.PointerEvent) => {
    dragRef.current = { sx: e.clientX, sy: e.clientY, cx: cam.x, cy: cam.y };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.clientX - drag.sx;
    const dy = e.clientY - drag.sy;
    if (!suppressClickRef.current && Math.hypot(dx, dy) < 5) return;
    if (!suppressClickRef.current) {
      suppressClickRef.current = true;
      frameRef.current?.setPointerCapture(e.pointerId);
    }
    setCam({ ...clampCam(drag.cx - dx, drag.cy - dy), animate: false });
  };
  const onPointerUp = () => {
    dragRef.current = null;
  };
  const onClickCapture = (e: React.MouseEvent) => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      e.stopPropagation();
      e.preventDefault();
    }
  };

  if (!room) return null;

  const findActor = (id: string): { kind: ActorKind; actor: ActorState } | null => {
    if (room.players[id]) return { kind: "player", actor: room.players[id] };
    if (room.enemies[id]) return { kind: "enemy", actor: room.enemies[id] };
    if (room.npcs[id]) return { kind: "npc", actor: room.npcs[id] };
    return null;
  };

  const onCellClick = (x: number, y: number) => {
    // Held bomb wins: any tile is a throw.
    if (held?.use === "bomb") {
      api.bomb(x, y);
      return;
    }
    const id = room.grid[y]?.[x];
    const hit = id ? findActor(id) : null;

    if (hit) {
      const { kind, actor } = hit;
      if (actor.id === playerId) {
        // Clicking yourself uses the held consumable (drink, eat, wear).
        if (held && held.use !== "sword") api.useSelectedOnSelf();
        return;
      }
      const hostile = actor.disposition === "hostile";
      if (hostile) {
        // The action model: no held sword, no swing.
        if (held?.use !== "sword") {
          api.note("ambient", `Your hands are empty. Hold your sword (1) to strike ${actor.name}.`);
        } else if (me && manhattan(me.position, actor.position) === 1) {
          api.attack(actor.id);
        } else {
          api.note("ambient", `Too far to strike ${actor.name}. Stand beside it (no diagonals).`);
        }
      } else if (kind === "npc") {
        if (me && manhattan(me.position, actor.position) === 1) api.openDialogue(actor as NpcState);
        else api.note("ambient", `Step beside ${actor.name} to talk.`);
      } else {
        api.note("ambient", `${actor.name} gives you a friendly nod.`);
      }
      return;
    }
    const obj = objectsAt.get(`${x},${y}`);
    if (obj) api.inspect(obj.id);
  };

  const cells = [];
  for (let y = 0; y < room.room.height; y++) {
    for (let x = 0; x < room.room.width; x++) {
      const wall = walls.has(`${x},${y}`);
      const id = room.grid[y]?.[x];
      const hit = id ? findActor(id) : null;
      const obj = !hit ? objectsAt.get(`${x},${y}`) : undefined;
      const isMe = hit?.actor.id === playerId;

      const classes = ["cell"];
      if (wall) classes.push("cell-wall");
      if (isMe) classes.push("cell-me");
      if (held?.use === "bomb" && !wall) classes.push("cell-bomb-target");
      else if (isMe && held && held.use !== "sword") classes.push("cell-self-target");
      else if (hit && !isMe) {
        classes.push(hit.actor.disposition === "hostile" ? "cell-hostile" : "cell-actionable");
      } else if (obj) classes.push("cell-actionable");

      cells.push(
        <div key={`${x},${y}`} className={classes.join(" ")} onClick={() => onCellClick(x, y)}>
          {hit && (
            <>
              <span className="entity-icon">{actorIcon(hit.kind, hit.actor, isMe)}</span>
              <span className="entity-name">{hit.actor.name}</span>
              <HpBar actor={hit.actor} />
              <span className={`disp-dot disp-${hit.actor.disposition}`} />
              {hit.kind === "npc" && (hit.actor as NpcState).party_owner_id === playerId && (
                <span className="party-badge" title="In your party">✦</span>
              )}
            </>
          )}
          {obj && (
            <>
              <span className="object-icon">{OBJECT_ICONS[obj.type] ?? "✨"}</span>
              <span className="object-name">{obj.label}</span>
            </>
          )}
        </div>,
      );
    }
  }

  const inCombat = room.room.mode === "combat";

  return (
    <section className="stage">
      <div
        className="grid-frame"
        ref={frameRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClickCapture={onClickCapture}
      >
        <div
          className="grid"
          ref={gridRef}
          style={{
            gridTemplateColumns: `repeat(${room.room.width}, var(--cell))`,
            transform: `translate3d(${-cam.x}px, ${-cam.y}px, 0)`,
            transition: cam.animate ? "transform 0.45s cubic-bezier(0.22, 1, 0.36, 1)" : "none",
          }}
        >
          {cells}
        </div>
        <div className={`edge-fade fade-left ${cam.fades.left ? "on" : ""}`} />
        <div className={`edge-fade fade-right ${cam.fades.right ? "on" : ""}`} />
        <div className={`edge-fade fade-top ${cam.fades.top ? "on" : ""}`} />
        <div className={`edge-fade fade-bottom ${cam.fades.bottom ? "on" : ""}`} />
      </div>

      {inCombat && (
        <div className="combat-actions">
          {actionLocked ? (
            <span className="waiting-note">
              Committed — waiting for{" "}
              {waitingFor
                .filter((id) => id !== playerId)
                .map((id) => room.players[id]?.name ?? "…")
                .join(", ") || "the round to resolve"}
              …
            </span>
          ) : (
            <button onClick={() => api.wait()}>Hold (skip turn)</button>
          )}
        </div>
      )}
    </section>
  );
}
