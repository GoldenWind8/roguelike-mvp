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
import { packOf, useGame, useGameApi } from "../store/gameStore";
import type { ActorState, NpcState, ObjectSummary } from "../net/types";

export const RECENTER_ROOM_EVENT = "emberhollow:recenter-room";
export const TOGGLE_FOOTPRINTS_EVENT = "emberhollow:toggle-footprints";

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
  noticeboard: "📜",
  broken_waystone: "🪨",
  orchard_press: "🍎",
  mill_ruin: "⚙️",
  roadside_shrine: "🕯️",
  tollhouse_ruin: "🏚️",
  empty_ledger_hook: "⛓️",
  barrow_stone: "🪦",
  grave_marker: "†",
  fieldsite_apparatus: "⚗️",
  blackened_cistern: "◉",
  lantern_mast: "🏮",
  black_reed_brazier: "🔥",
  crown_ledger_plinth: "📖",
  first_rot_memorial: "🕯️",
  drazna_lye_trough: "🪣",
  drazna_refugee_bundles: "💌",
  drazna_false_manifest: "🗒️",
  drazna_omitted_tablets: "🪧",
  drazna_crown_flood_order: "📜",
  drazna_preproclamation_roll: "📋",
  drazna_barge_plaque: "⚓",
  drazna_palace_drain: "🕳️",
  drazna_cut_dive_rope: "🪢",
  drazna_survivor_bunks: "🛏️",
  drazna_knock_wall: "🧱",
  drazna_dryline_chalk: "🖍️",
  drazna_listening_pipe: "📯",
  drazna_flooded_nursery: "🛏️",
  drazna_sluice_tools: "🛠️",
  drazna_pressure_gauge: "🎛️",
  drazna_black_key_hook: "🗝️",
  crate: "📦",
  cellar_door: "🕳️",
  door: "🚪",
  chest: "🧰",
  fire_barrel: "🛢️",
};

/** Chests wear their lifecycle (docs/LOOT.md): shut, emptied, or holding
 * items nobody could carry when it was opened. */
function objectIcon(obj: ObjectSummary): string {
  if (obj.type === "chest" && obj.opened) {
    return (obj.contents_count ?? 0) > 0 ? "💰" : "🕸️";
  }
  return OBJECT_ICONS[obj.type] ?? "✨";
}

function objectActionLabel(
  obj: ObjectSummary,
  withinReach: boolean,
): string {
  if (obj.type === "chest") {
    if (!withinReach) {
      return `Inspect ${obj.label}; step closer to ${obj.opened ? "look inside" : "open it"}`;
    }
    return obj.opened ? `Look inside ${obj.label}` : `Open ${obj.label}`;
  }
  if (!withinReach && obj.interaction) {
    const closerAction = {
      shop: "browse its wares",
      noticeboard: "read its notices",
      carriage: "use its carriage routes",
      situation: "attend to it",
    }[obj.interaction];
    return `Inspect ${obj.label}; step closer to ${closerAction}`;
  }
  switch (obj.interaction) {
    case "shop":
      return `Browse wares at ${obj.label}`;
    case "noticeboard":
      return `Read ${obj.label}`;
    case "carriage":
      return `Open carriage routes at ${obj.label}`;
    case "situation":
      return `Attend to ${obj.label}`;
    default:
      return `Inspect ${obj.label}`;
  }
}

// The server's targeting rule for attack AND talk: orthogonally adjacent
// (Manhattan distance 1) — diagonals don't count. Mirrored here only to give
// a friendly hint instead of a server error.
const manhattan = (a: [number, number], b: [number, number]) =>
  Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);

const objectCells = (obj: ObjectSummary): [number, number][] =>
  obj.occupied_cells.length > 0 ? obj.occupied_cells : [obj.position];

const distanceToObject = (position: [number, number], obj: ObjectSummary) =>
  Math.min(...objectCells(obj).map((cell) => manhattan(position, cell)));

/** The rectangle occupied by an object's artwork, expressed in grid cells.
 * Images are bottom-aligned and centred on the logical footprint, matching
 * the CSS renderer below. */
function objectVisualBounds(obj: ObjectSummary) {
  const occupied = objectCells(obj);
  const xs = occupied.map(([x]) => x);
  const ys = occupied.map(([, y]) => y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const logicalWidth = maxX - minX + 1;
  const logicalHeight = maxY - minY + 1;
  const [visualWidth, visualHeight] = obj.visual_size;
  const left = minX + (logicalWidth - visualWidth) / 2;
  const bottom = maxY + 1;
  return {
    minX,
    maxX,
    minY,
    maxY,
    logicalWidth,
    logicalHeight,
    visualWidth,
    visualHeight,
    left,
    right: left + visualWidth,
    top: bottom - visualHeight,
    bottom,
  };
}

/** Fade scenery only when an actor's feet are behind its baseline and their
 * cell falls under the visible artwork. An actor south of the baseline is in
 * front and keeps the object opaque. */
function objectOccludesActor(obj: ObjectSummary, actors: ActorState[]): boolean {
  const bounds = objectVisualBounds(obj);
  const occupied = new Set(objectCells(obj).map(([x, y]) => `${x},${y}`));
  return actors.some((actor) => {
    if (!actor.is_alive) return false;
    const [x, y] = actor.position;
    if (occupied.has(`${x},${y}`)) return false;
    const centreX = x + 0.5;
    const feetY = y + 0.8;
    return centreX > bounds.left
      && centreX < bounds.right
      && feetY > bounds.top
      && y <= bounds.maxY;
  });
}

function exitPresentation(position: [number, number], width: number, height: number) {
  const [x, y] = position;
  if (y === 0) return { glyph: "↑", edge: "top" };
  if (y === height - 1) return { glyph: "↓", edge: "bottom" };
  if (x === 0) return { glyph: "←", edge: "left" };
  if (x === width - 1) return { glyph: "→", edge: "right" };
  return { glyph: "◇", edge: "portal" };
}

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
  const {
    room,
    playerId,
    selectedSlot,
    chestOpenPending,
    actionLocked,
    waitingFor,
  } = useGame();
  const api = useGameApi();
  const stageRef = useRef<HTMLElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const previousRoomIdRef = useRef<number | null>(null);
  const [cam, setCam] = useState<Camera>({ x: 0, y: 0, animate: false, fades: NO_FADES });
  const [debugFootprints, setDebugFootprints] = useState(false);
  const dragRef = useRef<{ sx: number; sy: number; cx: number; cy: number } | null>(null);
  const suppressClickRef = useRef(false);

  const pack = packOf(room, playerId);
  const held = selectedSlot !== null ? pack[selectedSlot] ?? null : null;
  const me = room && playerId ? room.players[playerId] : null;
  // The client mirrors two server reach rules for friendly hints only (the
  // server re-validates everything): thrown range is item data, attack reach
  // comes from the equipped weapon (bare hands = 1).
  const throwRange = held?.item.type === "throwable" ? held.item.payload.throw_range ?? 1 : null;
  const weaponReach = pack.find((s) => s.equipped && s.item.type === "weapon")?.item.payload.range ?? 1;

  const walls = useMemo(() => {
    const set = new Set<string>();
    room?.walls.forEach(([x, y]) => set.add(`${x},${y}`));
    return set;
  }, [room?.walls]);

  const objectsAt = useMemo(() => {
    const map = new Map<string, ObjectSummary>();
    room?.objects.forEach((o) => {
      objectCells(o).forEach(([x, y]) => map.set(`${x},${y}`, o));
    });
    return map;
  }, [room?.objects]);

  const exitsAt = useMemo(() => {
    const map = new Map<string, {
      position: [number, number];
      label: string;
      frontier: boolean;
    }>();
    room?.exits.forEach((exit) => map.set(
      `${exit.position[0]},${exit.position[1]}`,
      { position: exit.position, label: exit.label, frontier: false },
    ));
    room?.frontier_exits?.forEach((exit) => map.set(
      `${exit.position[0]},${exit.position[1]}`,
      { position: exit.position, label: exit.label, frontier: true },
    ));
    return map;
  }, [room?.exits, room?.frontier_exits]);

  const livingActors = useMemo(
    () => room
      ? [...Object.values(room.players), ...Object.values(room.enemies), ...Object.values(room.npcs)]
          .filter((actor) => actor.is_alive)
      : [],
    [room],
  );

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
  const recenterCamera = useCallback((animate = true) => {
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
    setCam({ ...target, animate });
  }, [meX, meY, clampCam]);

  useEffect(() => {
    recenterCamera(true);
  }, [recenterCamera, room?.room.id, room?.room.width, room?.room.height]);

  useEffect(() => {
    const roomId = room?.room.id;
    if (roomId === undefined) return;
    const previousRoomId = previousRoomIdRef.current;
    previousRoomIdRef.current = roomId;
    if (previousRoomId === null || previousRoomId === roomId) return;
    const frame = window.requestAnimationFrame(() => {
      stageRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [room?.room.id]);

  useEffect(() => {
    const recenter = () => recenterCamera(true);
    const toggleFootprints = () => setDebugFootprints((shown) => !shown);
    window.addEventListener(RECENTER_ROOM_EVENT, recenter);
    window.addEventListener(TOGGLE_FOOTPRINTS_EVENT, toggleFootprints);
    return () => {
      window.removeEventListener(RECENTER_ROOM_EVENT, recenter);
      window.removeEventListener(TOGGLE_FOOTPRINTS_EVENT, toggleFootprints);
    };
  }, [recenterCamera]);

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
    if ((e.target as HTMLElement).closest(".camera-recenter")) {
      suppressClickRef.current = false;
      return;
    }
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

  const interactObject = (obj: ObjectSummary) => {
    // Chests open (or yield their waiting contents) on an adjacent click —
    // rolled at that moment, first-to-open (docs/LOOT.md). From afar, look.
    if (obj.type === "chest" && me && distanceToObject(me.position, obj) <= 1) {
      api.openChest(obj.id);
      return;
    }
    if (obj.interaction === "shop" && me && distanceToObject(me.position, obj) <= 1) {
      api.openShop(obj.id);
      return;
    }
    if (obj.interaction === "noticeboard" && me && distanceToObject(me.position, obj) <= 1) {
      api.openNoticeboard(obj.id);
      return;
    }
    if (obj.interaction === "carriage" && me && distanceToObject(me.position, obj) <= 1) {
      api.openCarriage(obj.id);
      return;
    }
    if (obj.interaction === "situation" && me && distanceToObject(me.position, obj) <= 1) {
      api.openSituation(obj.id);
      return;
    }
    api.inspect(obj.id);
  };

  const onCellClick = (x: number, y: number) => {
    // Held throwable wins: any tile in range is a throw.
    if (held && throwRange !== null && selectedSlot !== null) {
      if (me && manhattan(me.position, [x, y]) <= throwRange) {
        api.throwItem(selectedSlot, x, y);
      } else {
        api.note("ambient", `Too far — the ${held.item.name} carries ${throwRange} tiles.`);
      }
      return;
    }
    const id = room.grid[y]?.[x];
    const hit = id ? findActor(id) : null;

    if (hit) {
      const { kind, actor } = hit;
      if (actor.id === playerId) {
        // Clicking yourself uses the held item on yourself: drink/eat a
        // consumable, or equip/stow held gear.
        if (!held || selectedSlot === null) return;
        if (held.item.type === "consumable") api.consume(selectedSlot);
        else if (held.item.type !== "throwable") api.toggleEquip(selectedSlot);
        return;
      }
      const hostile = actor.disposition === "hostile";
      if (hostile) {
        // Weapons are EQUIPPED, not held: click a hostile in reach to strike
        // with whatever is readied (bare hands included).
        if (me && manhattan(me.position, actor.position) <= weaponReach) {
          api.attack(actor.id);
        } else {
          api.note("ambient", `Too far to strike ${actor.name}.${weaponReach === 1 ? " Stand beside it (no diagonals)." : ""}`);
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
    if (!obj) return;
    interactObject(obj);
  };

  const cells = [];
  for (let y = 0; y < room.room.height; y++) {
    for (let x = 0; x < room.room.width; x++) {
      const wall = walls.has(`${x},${y}`);
      const id = room.grid[y]?.[x];
      const hit = id ? findActor(id) : null;
      const obj = !hit ? objectsAt.get(`${x},${y}`) : undefined;
      const exit = exitsAt.get(`${x},${y}`);
      const isMe = hit?.actor.id === playerId;

      const classes = ["cell"];
      if (wall) classes.push("cell-wall");
      if (exit) classes.push("cell-exit");
      if (exit?.frontier) classes.push("cell-frontier-exit");
      if (obj) classes.push("cell-object-footprint");
      if (obj?.blocks_movement) classes.push("cell-object-blocking");
      if (hit) classes.push("cell-actor-footprint");
      if (isMe) classes.push("cell-me");
      const inThrowRange =
        throwRange !== null && me ? manhattan(me.position, [x, y]) <= throwRange : false;
      if (throwRange !== null && !wall) {
        if (inThrowRange) classes.push("cell-bomb-target");
      } else if (isMe && held && held.item.type !== "throwable") {
        classes.push("cell-self-target");
      } else if (hit && !isMe) {
        classes.push(hit.actor.disposition === "hostile" ? "cell-hostile" : "cell-actionable");
      } else if (obj) classes.push("cell-actionable");

      cells.push(
        <div
          key={`${x},${y}`}
          className={classes.join(" ")}
          style={{ gridColumn: x + 1, gridRow: y + 1 }}
          onClick={() => onCellClick(x, y)}
        >
          {exit && (() => {
            const presentation = exitPresentation(exit.position, room.room.width, room.room.height);
            return (
              <span
                className={`exit-marker exit-${presentation.edge} ${exit.frontier ? "exit-frontier" : ""}`}
                aria-label={exit.frontier ? `Untravelled road: ${exit.label}` : `Exit to ${exit.label}`}
              >
                <span className="exit-glyph">{presentation.glyph}</span>
                <span className="exit-label">{exit.label.replace(/^The\s+/i, "")}</span>
              </span>
            );
          })()}
          {hit && (
            <>
              {hit.actor.image ? (
                <button
                  className="entity-art-button"
                  type="button"
                  title={hit.kind === "npc" ? `Talk to ${hit.actor.name}` : hit.actor.name}
                  aria-label={hit.kind === "npc" ? `Talk to ${hit.actor.name}` : hit.actor.name}
                  style={{
                    width: `calc(var(--cell) * ${hit.actor.visual_size[0]})`,
                    height: `calc(var(--cell) * ${hit.actor.visual_size[1]})`,
                  }}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={(event) => {
                    event.stopPropagation();
                    onCellClick(x, y);
                  }}
                >
                  <img
                    className="entity-sprite"
                    src={hit.actor.image}
                    alt=""
                    draggable={false}
                  />
                </button>
              ) : (
                <span className="entity-icon">{actorIcon(hit.kind, hit.actor, isMe)}</span>
              )}
              <span className="entity-name">{hit.actor.name}</span>
              <HpBar actor={hit.actor} />
              <span className={`disp-dot disp-${hit.actor.disposition}`} />
              {hit.kind === "npc" && (hit.actor as NpcState).party_owner_id === playerId && (
                <span className="party-badge" title="In your party">✦</span>
              )}
            </>
          )}
        </div>,
      );
    }
  }

  // Objects are separate grid items laid over their logical footprint. Each
  // sprite is drawn once, even when its collision spans several cells; image
  // overhang is presentation only and never changes the server's rules.
  const objectRenders = room.objects.map((obj) => {
    const bounds = objectVisualBounds(obj);
    const fadesForActor = objectOccludesActor(obj, livingActors);
    const openingChest = obj.type === "chest" && chestOpenPending === obj.id;
    const actionLabel = openingChest
      ? `Opening ${obj.label}`
      : objectActionLabel(
          obj,
          Boolean(me && distanceToObject(me.position, obj) <= 1),
        );

    return (
      <div
        key={obj.id}
        className={`object-render ${fadesForActor ? "object-occluding-actor" : ""}`}
        style={{
          gridColumn: `${bounds.minX + 1} / span ${bounds.logicalWidth}`,
          gridRow: `${bounds.minY + 1} / span ${bounds.logicalHeight}`,
        }}
      >
        {obj.image ? (
          <button
            className="object-art-button"
            type="button"
            title={actionLabel}
            aria-label={actionLabel}
            aria-busy={openingChest}
            disabled={openingChest}
            style={{
              width: `${(bounds.visualWidth / bounds.logicalWidth) * 100}%`,
              height: `${(bounds.visualHeight / bounds.logicalHeight) * 100}%`,
            }}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              interactObject(obj);
            }}
          >
            <img
              className="object-sprite"
              src={obj.image}
              alt=""
              draggable={false}
            />
            <span className="sprite-tooltip">{obj.label}</span>
          </button>
        ) : (
          <button
            className="object-art-button object-fallback-button"
            type="button"
            title={actionLabel}
            aria-label={actionLabel}
            aria-busy={openingChest}
            disabled={openingChest}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              interactObject(obj);
            }}
          >
            <span className="object-icon">{objectIcon(obj)}</span>
            <span className="object-name">{obj.label}</span>
          </button>
        )}
        {obj.type === "chest" && (obj.contents_count ?? 0) > 0 && (
          <span className="slot-count object-count" title="Items waiting inside">{obj.contents_count}</span>
        )}
      </div>
    );
  });

  const inCombat = room.room.mode === "combat";

  return (
    <section
      ref={stageRef}
      className="stage"
      tabIndex={-1}
      aria-label={`${room.room.name} map`}
    >
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
          className={`grid ${debugFootprints ? "debug-footprints" : ""}`}
          ref={gridRef}
          style={{
            gridTemplateColumns: `repeat(${room.room.width}, var(--cell))`,
            gridTemplateRows: `repeat(${room.room.height}, var(--cell))`,
            transform: `translate3d(${-cam.x}px, ${-cam.y}px, 0)`,
            transition: cam.animate ? "transform 0.45s cubic-bezier(0.22, 1, 0.36, 1)" : "none",
          }}
        >
          {cells}
          {objectRenders}
        </div>
        <button
          className="camera-recenter"
          type="button"
          title="Recenter on your character (Home)"
          aria-label="Recenter on your character"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            recenterCamera(true);
          }}
        >
          ◎
        </button>
        {debugFootprints && (
          <div className="footprint-legend" aria-live="polite">
            <span><i className="legend-collision" /> collision</span>
            <span><i className="legend-visual" /> artwork</span>
          </div>
        )}
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
