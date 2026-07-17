/**
 * One store, two halves (FRONTEND_DESIGN.md "store/"):
 *   - a mirror of server state (room payload, dialogue transcript, inspection)
 *     that only ServerMessages may write, and
 *   - local UI state (armed bomb, music, selection) the server never sees.
 *
 * Components read via useGame() and act via useGameApi(); nobody touches the
 * socket directly.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import { MockGameSocket } from "../net/mockSocket";
import { RealGameSocket } from "../net/wsSocket";
import type { GameSocket } from "../net/socket";
import type {
  ConnectionStatus,
  GameEvent,
  NpcState,
  ObjectDetail,
  RoomStatePayload,
  ServerMessage,
} from "../net/types";
import { ambient } from "../audio/ambient";

// --- which world -------------------------------------------------------------
// The live server is the default; ?mock keeps the self-contained design-mockup
// world (no backend needed). Same UI, same protocol, different GameSocket.

export const USE_MOCK = new URLSearchParams(location.search).has("mock");

const TOKEN_KEY = "emberhollow.token";
const NAME_KEY = "emberhollow.username";

type AuthResult =
  | { ok: true; token: string; username: string }
  | { ok: false; message: string };

async function postAuth(path: "/login" | "/register", username: string, password: string): Promise<AuthResult> {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, message: String(body.detail ?? "Something went wrong.") };
    return { ok: true, token: body.token, username: body.username };
  } catch {
    return { ok: false, message: "The hall is unreachable — is the server running?" };
  }
}

// --- log formatting -------------------------------------------------------------

export type LogKind =
  | "ambient" | "join" | "attack" | "death" | "calm"
  | "danger" | "heal" | "npc" | "error" | "item";

export interface LogLine {
  id: number;
  kind: LogKind;
  text: string;
}

let logSeq = 0;

/** Turn one broadcast's events into chronicle lines. Batch-aware because the
 * engine narrates an attack twice — the *_attacked intent event and then the
 * entity_damaged effect event — and only one of them should speak. Names are
 * resolved from the state that rides in the same message (events carry ids;
 * the dead stay in the payload, so lookups survive their own death lines). */
function formatEvents(events: GameEvent[], state: RoomStatePayload): (LogLine | null)[] {
  const nameOf = (id: unknown): string => {
    if (typeof id !== "string") return "someone";
    const actor = state.players[id] ?? state.enemies[id] ?? state.npcs[id];
    return actor?.name ?? "someone";
  };
  const narratedTargets = new Set(
    events.filter((e) => e.event_type.endsWith("_attacked")).map((e) => e.data.target_id),
  );
  const line = (kind: LogKind, text: string): LogLine => ({ id: ++logSeq, kind, text });

  return events.map((e) => {
    const d = e.data as Record<string, string | number | null>;
    switch (e.event_type) {
      case "player_joined":
        return line("join", `${d.name} steps in from the cold.`);
      case "player_left":
        return line("ambient", `${d.name} slips out into the night.`);
      case "player_entered_door":
        return line("ambient", `${d.name} passes through the door.`);
      case "player_attacked":
        return line("attack", `${nameOf(d.attacker_id)} strikes ${nameOf(d.target_id)} for ${d.damage}.`);
      case "enemy_attacked":
      case "npc_attacked":
        return line("attack", `${d.attacker_name} strikes ${nameOf(d.target_id)} for ${d.damage}.`);
      case "entity_damaged":
        // Un-narrated damage (bomb blasts today) still gets a line.
        if (narratedTargets.has(d.target_id)) return null;
        return line("attack", `The blast catches ${nameOf(d.target_id)} for ${d.damage}.`);
      case "player_died":
      case "enemy_died":
      case "npc_died":
        return line("death", `${nameOf(d.target_id)} falls!`);
      case "disposition_changed":
        if (d.disposition === "hostile") return line("danger", `${nameOf(d.target_id)} turns hostile!`);
        if (d.disposition === "friendly") return line("calm", `${nameOf(d.target_id)} warms to you.`);
        return line("calm", `${nameOf(d.target_id)} lets out a breath and calms down.`);
      case "party_changed":
        return d.owner_id
          ? line("calm", `${nameOf(d.target_id)} falls in beside ${nameOf(d.owner_id)}.`)
          : line("ambient", `${nameOf(d.target_id)} strikes out alone again.`);
      case "room_mode_changed":
        return d.mode === "combat"
          ? line("danger", "Steel is drawn. The warmth drains from the hall.")
          : line("calm", "The hall grows quiet again. The hearth settles.");
      case "bomb_thrown":
        return line("danger", `${nameOf(d.player_id)} lobs a bomb — fire blooms across the floorboards!`);

      // Mock-only events (no backend counterpart yet — see net/types.ts).
      case "ambient":
        return line("ambient", String(d.text));
      case "revive":
        return line("heal", String(d.text));
      case "enemy_spawned":
        return line("danger", `A ${d.name} skitters up from the dark!`);
      case "heal":
        return line("heal", `${d.by} tends ${d.name}'s wounds (+${d.amount}).`);

      // player/enemy/npc_moved, round_started, invalid_action: visible on the
      // grid or delivered as a direct error — the chronicle stays quiet.
      default:
        return null;
    }
  });
}

// --- the belt (mock-only: no backend contract yet) --------------------------------
// Ten fixed slots, Minecraft-style. This is design-ahead for the intended
// action model: EVERY act on the world is "select an item, then click a
// target" — a sword swing included. Slots keep their position; an emptied
// slot goes null rather than compacting.

export const SLOT_COUNT = 10;

export interface InventoryItem {
  id: string;
  icon: string;
  name: string;
  count: number;
  description: string;
  use: "sword" | "heal" | "bomb" | "passive";
  healAmount?: number;
  /** Shown above the belt while this item is in hand. */
  hint: string;
}

const STARTING_SLOTS: (InventoryItem | null)[] = [
  {
    id: "sword", icon: "🗡️", name: "Rusted Sword", count: 1,
    description: "It has seen better decades, but it still argues convincingly.",
    use: "sword",
    hint: "click an enemy beside you to strike",
  },
  {
    id: "draught", icon: "🧪", name: "Healing Draught", count: 2,
    description: "Herbs, honey, and something Mara won't name. Restores 30 vigor.",
    use: "heal", healAmount: 30,
    hint: "click yourself to drink it",
  },
  {
    id: "bread", icon: "🍞", name: "Hearthbread", count: 3,
    description: "Still warm from Gorrik's oven. Restores 10 vigor and most moods.",
    use: "heal", healAmount: 10,
    hint: "click yourself to eat it",
  },
  {
    id: "bomb", icon: "💣", name: "Ember Bomb", count: 2,
    description: "Choose it, then choose a tile. Loud. Final.",
    use: "bomb",
    hint: "click any tile to throw it",
  },
  {
    id: "cloak", icon: "🧥", name: "Woolen Cloak", count: 1,
    description: "Worn, patched, beloved. Keeps out drafts and dread alike.",
    use: "passive",
    hint: "click yourself to pull it tighter",
  },
  ...Array.from({ length: SLOT_COUNT - 5 }, () => null),
];

// --- state ------------------------------------------------------------------------

export interface DialogueLine {
  who: "player" | "npc";
  text: string;
}

interface DialogueState {
  npcId: string;
  lines: DialogueLine[];
  pending: boolean;
}

export interface GameState {
  screen: "login" | "game";
  username: string;
  playerId: string | null;
  connection: ConnectionStatus;
  room: RoomStatePayload | null;
  log: LogLine[];
  dialogue: DialogueState | null;
  inspection: ObjectDetail | null;
  slots: (InventoryItem | null)[];
  selectedSlot: number | null;
  musicOn: boolean;
  /** Turn-based combat: your action is submitted; the round hasn't resolved. */
  actionLocked: boolean;
  /** Player ids the round is still waiting on (turn-based rooms only). */
  waitingFor: string[];
  /** Why the server bounced us back to the login screen, if it did. */
  loginError: string | null;
}

const initialState: GameState = {
  screen: "login",
  username: "",
  playerId: null,
  connection: "disconnected",
  room: null,
  log: [],
  dialogue: null,
  inspection: null,
  slots: STARTING_SLOTS,
  selectedSlot: null,
  musicOn: true,
  actionLocked: false,
  waitingFor: [],
  loginError: null,
};

type Action =
  | { type: "logged_in"; username: string }
  | { type: "auth_failed"; message: string }
  | { type: "status"; status: ConnectionStatus }
  | { type: "server"; msg: ServerMessage }
  | { type: "open_dialogue"; npc: NpcState }
  | { type: "close_dialogue" }
  | { type: "player_said"; text: string }
  | { type: "close_inspection" }
  | { type: "select_slot"; index: number | null }
  | { type: "consume_slot"; index: number; note?: string }
  | { type: "set_music"; on: boolean }
  | { type: "log"; kind: LogKind; text: string };

const MAX_LOG = 80;

function appendLog(log: LogLine[], lines: (LogLine | null)[]): LogLine[] {
  const add = lines.filter((l): l is LogLine => l !== null);
  if (add.length === 0) return log;
  return [...log, ...add].slice(-MAX_LOG);
}

function reduce(state: GameState, action: Action): GameState {
  switch (action.type) {
    case "logged_in":
      return { ...state, username: action.username, screen: "game", loginError: null };
    case "auth_failed":
      // Back to the front door, but keep local preferences (music).
      return { ...initialState, musicOn: state.musicOn, loginError: action.message };
    case "status":
      return { ...state, connection: action.status };
    case "server":
      return reduceServer(state, action.msg);
    case "open_dialogue":
      if (state.dialogue?.npcId === action.npc.id) return state;
      return { ...state, dialogue: { npcId: action.npc.id, lines: [], pending: false }, inspection: null };
    case "close_dialogue":
      return { ...state, dialogue: null };
    case "player_said":
      if (!state.dialogue) return state;
      return {
        ...state,
        dialogue: {
          ...state.dialogue,
          pending: true,
          lines: [...state.dialogue.lines, { who: "player", text: action.text }],
        },
      };
    case "close_inspection":
      return { ...state, inspection: null };
    case "select_slot": {
      // Clicking the held slot puts it away; empty slots can't be held.
      if (action.index !== null && !state.slots[action.index]) return state;
      const index = action.index === state.selectedSlot ? null : action.index;
      return { ...state, selectedSlot: index };
    }
    case "consume_slot": {
      const slots = state.slots.map((it, i) => {
        if (i !== action.index || !it) return it;
        return it.count > 1 ? { ...it, count: it.count - 1 } : null;
      });
      const log = action.note
        ? appendLog(state.log, [{ id: ++logSeq, kind: "item", text: action.note }])
        : state.log;
      const selectedSlot = slots[state.selectedSlot ?? -1] ? state.selectedSlot : null;
      return { ...state, slots, selectedSlot, log };
    }
    case "set_music":
      return { ...state, musicOn: action.on };
    case "log":
      return { ...state, log: appendLog(state.log, [{ id: ++logSeq, kind: action.kind, text: action.text }]) };
  }
}

function reduceServer(state: GameState, msg: ServerMessage): GameState {
  switch (msg.type) {
    case "join_ack":
      // The server's username is authoritative (a resumed session joins
      // before the login form ever ran).
      return { ...state, playerId: msg.player_id, username: msg.username, room: msg.state };
    case "state_update":
    case "room_changed": {
      let next = {
        ...state,
        room: msg.state,
        log: appendLog(state.log, formatEvents(msg.events, msg.state)),
        // A broadcast state means the round resolved (or the world moved on).
        actionLocked: false,
        waitingFor: [],
      };
      // If the NPC we're talking to died, the conversation is over.
      if (next.dialogue && !msg.state.npcs[next.dialogue.npcId]?.is_alive) {
        next = { ...next, dialogue: null };
      }
      return next;
    }
    case "npc_dialogue": {
      if (!state.dialogue || state.dialogue.npcId !== msg.npc_id) return state;
      return {
        ...state,
        dialogue: {
          ...state.dialogue,
          pending: false,
          lines: [...state.dialogue.lines, { who: "npc", text: msg.text }],
        },
      };
    }
    case "object_inspection":
      return { ...state, inspection: msg.object, dialogue: null };
    case "error":
      return {
        ...state,
        log: appendLog(state.log, [{ id: ++logSeq, kind: "error", text: msg.message }]),
        dialogue: state.dialogue ? { ...state.dialogue, pending: false } : null,
      };
    case "action_locked":
      return { ...state, actionLocked: true };
    case "waiting_for":
      return { ...state, waitingFor: msg.player_ids };
    case "world_reset":
      return state; // handled as a side effect (full reload) before dispatch
  }
}

// --- context ------------------------------------------------------------------------

interface GameApi {
  /** Log in (HTTP → token → WS join). Resolves to an error message, or null
   * on success. In mock mode any name gets in and it resolves immediately. */
  login(username: string, password: string): Promise<string | null>;
  /** Register a new account, then join. Same result contract as login. */
  register(username: string, password: string): Promise<string | null>;
  /** Rejoin with a stored session token; false if there is none. */
  resume(): boolean;
  /** Forget the stored session and reload to the front door. */
  logout(): void;
  /** Death recovery (real server): reconnect with the same token — a dead
   * character is saved at the disconnect edge and respawns fresh. */
  rejoin(): void;
  move(dx: number, dy: number): void;
  attack(targetId: string): void;
  wait(): void;
  /** Throw the held bomb at a tile; consumes one from its slot. */
  bomb(x: number, y: number): void;
  /** Hold/put away a belt slot (toggle). Pass null to empty your hands. */
  selectSlot(index: number | null): void;
  /** Shortcut: hold whichever slot has bombs (the B key). */
  selectBombSlot(): void;
  /** Use the held consumable on yourself (drink/eat/wear). */
  useSelectedOnSelf(): void;
  inspect(objectId: string): void;
  openDialogue(npc: NpcState): void;
  closeDialogue(): void;
  closeInspection(): void;
  talk(text: string): void;
  toggleMusic(): void;
  /** Local flavor/hint line in the chronicle; never touches the server. */
  note(kind: LogKind, text: string): void;
}

const StateContext = createContext<GameState>(initialState);
const ApiContext = createContext<GameApi | null>(null);

export function GameProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reduce, initialState);
  const socketRef = useRef<GameSocket | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => () => socketRef.current?.close(), []);

  const api = useMemo<GameApi>(() => {
    const handleMessage = (msg: ServerMessage) => {
      // Two messages carry side effects no reducer should own:
      if (msg.type === "error" && msg.code === "auth") {
        // The stored token is dead (forged, or the db was reset) — forget it
        // and put the login form back up with the server's reason.
        localStorage.removeItem(TOKEN_KEY);
        socketRef.current?.close();
        socketRef.current = null;
        dispatch({ type: "auth_failed", message: msg.message });
        return;
      }
      if (msg.type === "world_reset") {
        location.reload();
        return;
      }
      dispatch({ type: "server", msg });
    };

    const socket = () => {
      if (!socketRef.current) {
        // The integration seam: same interface, two worlds (see USE_MOCK).
        socketRef.current = USE_MOCK ? new MockGameSocket() : new RealGameSocket();
        socketRef.current.connect(handleMessage, (status) => dispatch({ type: "status", status }));
      }
      return socketRef.current;
    };

    const joinAs = (username: string, token: string) => {
      dispatch({ type: "logged_in", username });
      socket().send({ type: "join", token });
      if (stateRef.current.musicOn) ambient.start();
    };

    const authThenJoin = async (
      path: "/login" | "/register",
      username: string,
      password: string,
    ): Promise<string | null> => {
      if (USE_MOCK) {
        joinAs(username, username);
        return null;
      }
      const result = await postAuth(path, username, password);
      if (!result.ok) return result.message;
      localStorage.setItem(TOKEN_KEY, result.token);
      localStorage.setItem(NAME_KEY, result.username);
      joinAs(result.username, result.token);
      return null;
    };

    /** In a turn-based round your one action is already in — swallow the
     * input locally instead of bouncing an error off the server. */
    const lockedThisRound = () => {
      if (!stateRef.current.actionLocked) return false;
      dispatch({
        type: "log",
        kind: "ambient",
        text: "Your move is committed — the round resolves when everyone has acted.",
      });
      return true;
    };

    return {
      login: (username, password) => authThenJoin("/login", username, password),
      register: (username, password) => authThenJoin("/register", username, password),
      resume() {
        if (USE_MOCK) return false;
        const token = localStorage.getItem(TOKEN_KEY);
        if (!token) return false;
        joinAs(localStorage.getItem(NAME_KEY) ?? "…", token);
        return true;
      },
      logout() {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(NAME_KEY);
        location.reload();
      },
      rejoin() {
        const token = localStorage.getItem(TOKEN_KEY);
        if (USE_MOCK || !token) return;
        socketRef.current?.close();
        socketRef.current = null;
        // A beat between close and rejoin: the server saves the leaver and
        // frees the one-connection-per-account slot at the disconnect edge.
        window.setTimeout(() => socket().send({ type: "join", token }), 900);
      },
      move(dx, dy) {
        if (lockedThisRound()) return;
        socket().send({ type: "action", action_type: "move", direction: [dx, dy] });
      },
      attack(targetId) {
        if (lockedThisRound()) return;
        socket().send({ type: "action", action_type: "attack", target_id: targetId });
      },
      wait() {
        if (lockedThisRound()) return;
        socket().send({ type: "action", action_type: "wait" });
      },
      bomb(x, y) {
        if (lockedThisRound()) return;
        const { slots } = stateRef.current;
        const index = slots.findIndex((it) => it?.use === "bomb");
        if (index < 0) return;
        socket().send({ type: "action", action_type: "bomb", target_tile: [x, y] });
        dispatch({ type: "consume_slot", index });
        dispatch({ type: "select_slot", index: null });
      },
      selectSlot(index) {
        dispatch({ type: "select_slot", index });
      },
      selectBombSlot() {
        const index = stateRef.current.slots.findIndex((it) => it?.use === "bomb");
        if (index < 0) {
          dispatch({ type: "log", kind: "error", text: "You are out of bombs." });
          return;
        }
        dispatch({ type: "select_slot", index });
      },
      useSelectedOnSelf() {
        const { slots, selectedSlot } = stateRef.current;
        const item = selectedSlot !== null ? slots[selectedSlot] : null;
        if (!item || selectedSlot === null) return;
        if (item.use === "heal" && item.healAmount) {
          if (!USE_MOCK) {
            // Inventory has no server contract yet (kept as visible debt —
            // see README "mock-only"); don't burn the item on a no-op.
            dispatch({
              type: "log",
              kind: "item",
              text: `The ${item.name} waits for its moment — only sword and bomb reach the real world so far.`,
            });
            dispatch({ type: "select_slot", index: null });
            return;
          }
          socket().send({ type: "mock_use_item", item_id: item.id, heal: item.healAmount });
          dispatch({
            type: "consume_slot",
            index: selectedSlot,
            note: `You use the ${item.name} (+${item.healAmount} vigor).`,
          });
          dispatch({ type: "select_slot", index: null });
        } else if (item.use === "passive") {
          dispatch({ type: "log", kind: "item", text: "You pull the cloak tighter. Better already." });
          dispatch({ type: "select_slot", index: null });
        }
      },
      inspect(objectId) {
        socket().send({ type: "inspect_object", object_id: objectId });
      },
      openDialogue(npc) {
        dispatch({ type: "open_dialogue", npc });
      },
      closeDialogue() {
        dispatch({ type: "close_dialogue" });
      },
      closeInspection() {
        dispatch({ type: "close_inspection" });
      },
      talk(text) {
        const d = stateRef.current.dialogue;
        if (!d || d.pending || !text.trim()) return;
        dispatch({ type: "player_said", text });
        socket().send({ type: "talk", npc_id: d.npcId, text });
      },
      toggleMusic() {
        const on = !stateRef.current.musicOn;
        dispatch({ type: "set_music", on });
        if (on) ambient.start();
        else ambient.stop();
      },
      note(kind, text) {
        dispatch({ type: "log", kind, text });
      },
    };
  }, []);

  return (
    <StateContext.Provider value={state}>
      <ApiContext.Provider value={api}>{children}</ApiContext.Provider>
    </StateContext.Provider>
  );
}

export function useGame(): GameState {
  return useContext(StateContext);
}

export function useGameApi(): GameApi {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useGameApi must be used inside GameProvider");
  return api;
}
