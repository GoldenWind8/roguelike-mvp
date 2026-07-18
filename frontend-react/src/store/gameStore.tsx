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
import { RealGameSocket } from "../net/wsSocket";
import type { GameSocket } from "../net/socket";
import type {
  ChestFind,
  ConnectionStatus,
  GameEvent,
  InventorySlot,
  ItemView,
  NpcState,
  ObjectDetail,
  RoomStatePayload,
  ServerMessage,
} from "../net/types";
import { ambient } from "../audio/ambient";

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

  const itemOf = (e: GameEvent): ItemView => e.data.item as ItemView;

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
      // --- loot system (docs/LOOT.md) ---
      case "chest_opened": {
        const finds = (e.data.items as ChestFind[]) ?? [];
        const names = finds.map((f) => `${f.item.art.value} ${f.item.name}`).join(", ");
        return line("item", `${nameOf(d.player_id)} pries the chest open — ${names}!`);
      }
      case "chest_looted":
        return line("item", `${nameOf(d.player_id)} takes the ${itemOf(e).art.value} ${itemOf(e).name} from the chest.`);
      case "item_generated":
        return line("danger", `✨ Something never seen before takes shape: ${itemOf(e).art.value} ${itemOf(e).name} (${itemOf(e).rarity})!`);
      case "item_consumed":
        return line("item", `${nameOf(d.player_id)} uses the ${itemOf(e).name}.`);
      case "item_thrown":
        return line("danger", `${nameOf(d.player_id)} hurls the ${itemOf(e).name}!`);
      case "item_equipped":
        return line("item", `${nameOf(d.player_id)} readies the ${itemOf(e).name}.`);
      case "item_unequipped":
        return line("item", `${nameOf(d.player_id)} stows the ${itemOf(e).name}.`);
      case "entity_healed":
        if (!d.amount) return null;
        return line("heal", `${nameOf(d.target_id)} recovers ${d.amount} vigor.`);
      case "hunger_restored":
        if (!d.amount) return null;
        return line("heal", `${nameOf(d.target_id)} eats well (+${d.amount} belly).`);
      case "player_starving":
        return line("danger", `${nameOf(d.target_id)} is starving! Find food, fast.`);
      case "effect_applied": {
        const sign = Number(d.amount) > 0 ? "+" : "";
        const kind: LogKind = Number(d.amount) > 0 ? "heal" : "danger";
        return line(kind, `The ${d.source} takes hold of ${nameOf(d.target_id)} (${sign}${d.amount} ${String(d.stat).replace("_", " ")}).`);
      }
      case "effect_expired":
        return line("ambient", `The ${d.source} fades from ${nameOf(d.target_id)}.`);

      // player/enemy/npc_moved, round_started, invalid_action: visible on the
      // grid or delivered as a direct error — the chronicle stays quiet.
      default:
        return null;
    }
  });
}

// --- the belt ---------------------------------------------------------------------
// Ten fixed slots (config.INVENTORY_SLOTS server-side). The pack itself is
// SERVER state — it rides on your player in every broadcast; the only local
// piece is which slot you're holding (selectedSlot). The action model:
// hold an item (1–0 or click), then click a target — yourself to drink,
// a tile to throw. Equip/unequip is a right-click (or E), no holding needed.

export const SLOT_COUNT = 10;

/** A popup card: a chest find plus who has taken it (name), if anyone. */
export interface LootFind extends ChestFind {
  takenBy?: string;
}

/** The local player's pack out of a room payload ([] before join). */
export function packOf(room: RoomStatePayload | null, playerId: string | null): InventorySlot[] {
  if (!room || !playerId) return [];
  return room.players[playerId]?.inventory ?? [];
}

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
  /** Held pack slot index (local UI state; the pack itself lives in `room`). */
  selectedSlot: number | null;
  /** The chest selection popup: what waits in the chest you just opened (or
   * peeked into), with per-item taken state kept live by chest_looted
   * broadcasts — so two players at one chest see each other's grabs.
   * Local UI state; closing it never talks to the server. */
  lootReveal: { objectId: string; finds: LootFind[] } | null;
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
  selectedSlot: null,
  lootReveal: null,
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
  | { type: "close_loot" }
  | { type: "select_slot"; index: number | null }
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
    case "close_loot":
      return { ...state, lootReveal: null };
    case "select_slot": {
      // Clicking the held slot puts it away; empty slots can't be held.
      if (action.index !== null && !packOf(state.room, state.playerId)[action.index]) return state;
      const index = action.index === state.selectedSlot ? null : action.index;
      return { ...state, selectedSlot: index };
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
      // Chest popup lifecycle rides in the broadcasts everyone gets: YOUR
      // open raises it; ANYONE's take marks a card taken (players at one
      // chest watch each other grab).
      for (const e of msg.events) {
        if (e.event_type === "chest_opened" && e.data.player_id === state.playerId) {
          next = {
            ...next,
            lootReveal: {
              objectId: String(e.data.object_id),
              finds: ((e.data.items as ChestFind[]) ?? []).map((f) => ({ ...f })),
            },
            inspection: null,
          };
        } else if (e.event_type === "chest_looted"
            && next.lootReveal
            && next.lootReveal.objectId === e.data.object_id) {
          const taken = e.data.item as ItemView;
          const taker = msg.state.players[String(e.data.player_id)]?.name ?? "someone";
          const finds = [...next.lootReveal.finds];
          const idx = finds.findIndex((f) => !f.takenBy && f.item.id === taken.id);
          if (idx >= 0) finds[idx] = { ...finds[idx], takenBy: taker };
          next = { ...next, lootReveal: { ...next.lootReveal, finds } };
        }
      }
      // If the NPC we're talking to died, the conversation is over.
      if (next.dialogue && !msg.state.npcs[next.dialogue.npcId]?.is_alive) {
        next = { ...next, dialogue: null };
      }
      // The pack is server state: if the held slot vanished (last potion
      // drunk, stack spent), the hand empties with it.
      if (next.selectedSlot !== null && !packOf(msg.state, state.playerId)[next.selectedSlot]) {
        next = { ...next, selectedSlot: null };
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
    case "chest_contents":
      // Peeking into an already-opened chest: same popup, current leftovers.
      return {
        ...state,
        lootReveal: { objectId: msg.object_id, finds: msg.items.map((f) => ({ ...f })) },
        inspection: null,
      };
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
   * on success. */
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
  /** Drink/eat pack slot N on yourself (a real round action). */
  consume(slot: number): void;
  /** Arc pack slot N at a tile (a real round action; range is item data). */
  throwItem(slot: number, x: number, y: number): void;
  /** Equip or unequip slot N (free, outside the round economy). */
  toggleEquip(slot: number): void;
  /** Open a chest (first open rolls; later opens show what's left). */
  openChest(objectId: string): void;
  /** Take one chosen item from an opened chest (the popup's Take button).
   * `index` is the item's current position among the chest's leftovers. */
  takeItem(objectId: string, index: number, itemId: number): void;
  /** Hold/put away a belt slot (toggle). Pass null to empty your hands. */
  selectSlot(index: number | null): void;
  inspect(objectId: string): void;
  openDialogue(npc: NpcState): void;
  closeDialogue(): void;
  closeInspection(): void;
  /** Dismiss the chest-reveal popup. */
  closeLoot(): void;
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
        socketRef.current = new RealGameSocket();
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
        if (!token) return;
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
      consume(slot) {
        if (lockedThisRound()) return;
        socket().send({ type: "action", action_type: "consume", slot });
        dispatch({ type: "select_slot", index: null });
      },
      throwItem(slot, x, y) {
        if (lockedThisRound()) return;
        socket().send({ type: "action", action_type: "throw", slot, target_tile: [x, y] });
        dispatch({ type: "select_slot", index: null });
      },
      toggleEquip(slot) {
        const pack = packOf(stateRef.current.room, stateRef.current.playerId);
        const held = pack[slot];
        if (!held) return;
        if (held.item.type !== "weapon" && held.item.type !== "wearable") {
          dispatch({ type: "log", kind: "ambient", text: `The ${held.item.name} isn't something you wear.` });
          return;
        }
        socket().send({ type: held.equipped ? "unequip" : "equip", slot });
      },
      openChest(objectId) {
        socket().send({ type: "open_chest", object_id: objectId });
      },
      takeItem(objectId, index, itemId) {
        socket().send({ type: "take_item", object_id: objectId, index, item_id: itemId });
      },
      selectSlot(index) {
        dispatch({ type: "select_slot", index });
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
      closeLoot() {
        dispatch({ type: "close_loot" });
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
