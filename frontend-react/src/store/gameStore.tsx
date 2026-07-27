/**
 * One store, two halves (FRONTEND_DESIGN.md "store/"):
 *   - a mirror of server state (room payload, dialogue transcript, inspection)
 *     that only ServerMessages may write, and
 *   - local UI state (held belt slot, music, selection) the server never sees.
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
  CarriageView,
  ChestFind,
  ConnectionStatus,
  GameEvent,
  InventorySlot,
  ItemView,
  KnownNpcView,
  NoticeboardView,
  NpcState,
  ObjectDetail,
  RoomStatePayload,
  RumorView,
  ServerMessage,
  ShopView,
  SituationView,
  WorldChronicleEntry,
  WorldTimeView,
} from "../net/types";
import { ambient } from "../audio/ambient";
import { itemLogLabel } from "../ui/ItemArt";

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
  | "danger" | "heal" | "npc" | "error" | "item" | "discovery";

export interface LogLine {
  id: number;
  kind: LogKind;
  text: string;
}

let logSeq = 0;
let discoverySeq = 0;

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
        // Attacks narrate their own damage. Other causes still need one
        // truthful line: starvation is not an explosion.
        if (narratedTargets.has(d.target_id)) return null;
        if (d.cause === "starvation") {
          return line(
            "danger",
            `${nameOf(d.target_id)} loses ${d.damage} vigor to starvation.`,
          );
        }
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
        const names = finds.map((f) => itemLogLabel(f.item)).join(", ");
        return line("item", `${nameOf(d.player_id)} pries the chest open — ${names}!`);
      }
      case "chest_looted":
        return line("item", `${nameOf(d.player_id)} takes the ${itemLogLabel(itemOf(e))} from the chest.`);
      case "shop_purchased":
        return line("item", `${nameOf(d.player_id)} buys ${itemLogLabel(itemOf(e))} for ${d.price} coins.`);
      case "item_sold":
        return line("item", `${nameOf(d.player_id)} sells ${itemLogLabel(itemOf(e))} for ${d.price} coins.`);
      case "item_generated":
        return line("danger", `✨ Something never seen before takes shape: ${itemLogLabel(itemOf(e))} (${itemOf(e).rarity})!`);
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
// a tile to throw. Equip/unequip is a right-click (or R), no holding needed.

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

export interface DiscoveryToastState {
  id: number;
  name: string;
  depth: number;
  biome: string;
  majorRegion: string;
}

export type WorldDrawerTab = "rumors" | "chronicle" | "people";
export type ProximityOpenKind =
  | "shop"
  | "noticeboard"
  | "carriage"
  | "situation";

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
  chestOpenPending: string | null;
  lootPendingCards: number[];
  proximityOpenPending: {
    kind: ProximityOpenKind;
    objectId: string;
  } | null;
  shop: ShopView | null;
  shopPending: "buy" | "sell" | null;
  noticeboard: NoticeboardView | null;
  carriage: CarriageView | null;
  carriagePending: "name" | "travel" | null;
  situation: SituationView | null;
  situationPending: boolean;
  discoveryToast: DiscoveryToastState | null;
  /** Player-private knowledge supplied by world_sync and its deltas. */
  worldTime: WorldTimeView | null;
  rumors: RumorView[];
  worldChronicle: WorldChronicleEntry[];
  knownPeople: KnownNpcView[];
  worldUnread: number;
  worldDrawerOpen: boolean;
  worldDrawerTab: WorldDrawerTab;
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
  chestOpenPending: null,
  lootPendingCards: [],
  proximityOpenPending: null,
  shop: null,
  shopPending: null,
  noticeboard: null,
  carriage: null,
  carriagePending: null,
  situation: null,
  situationPending: false,
  discoveryToast: null,
  worldTime: null,
  rumors: [],
  worldChronicle: [],
  knownPeople: [],
  worldUnread: 0,
  worldDrawerOpen: false,
  worldDrawerTab: "rumors",
  musicOn: true,
  actionLocked: false,
  waitingFor: [],
  loginError: null,
};

type Action =
  | { type: "logged_in"; username: string }
  | { type: "logged_out" }
  | { type: "auth_failed"; message: string }
  | { type: "status"; status: ConnectionStatus }
  | { type: "server"; msg: ServerMessage }
  | { type: "open_dialogue"; npc: NpcState }
  | { type: "close_dialogue" }
  | { type: "player_said"; text: string }
  | { type: "close_inspection" }
  | { type: "close_loot" }
  | { type: "chest_open_pending"; objectId: string }
  | { type: "loot_card_pending"; objectId: string; cardIndex: number }
  | {
      type: "proximity_open_pending";
      kind: ProximityOpenKind;
      objectId: string;
    }
  | { type: "close_shop" }
  | { type: "shop_pending"; pending: "buy" | "sell" }
  | { type: "close_noticeboard" }
  | { type: "close_carriage" }
  | { type: "carriage_pending"; pending: "name" | "travel" }
  | { type: "close_situation" }
  | { type: "situation_pending" }
  | { type: "dismiss_discovery"; id: number }
  | { type: "open_world_drawer"; tab?: WorldDrawerTab }
  | { type: "close_world_drawer" }
  | { type: "set_world_drawer_tab"; tab: WorldDrawerTab }
  | { type: "select_slot"; index: number | null }
  | { type: "set_music"; on: boolean }
  | { type: "log"; kind: LogKind; text: string };

const MAX_LOG = 80;
const MAX_WORLD_CHRONICLE = 120;

function appendLog(log: LogLine[], lines: (LogLine | null)[]): LogLine[] {
  const add = lines.filter((l): l is LogLine => l !== null);
  if (add.length === 0) return log;
  return [...log, ...add].slice(-MAX_LOG);
}

function upsertById<T>(items: T[], item: T, key: keyof T = "id" as keyof T): T[] {
  const index = items.findIndex((candidate) => candidate[key] === item[key]);
  if (index < 0) return [...items, item];
  const next = [...items];
  next[index] = item;
  return next;
}

/** A full sync is a state snapshot, not proof that the player opened the
 * corresponding page. Keep local unread markers until that page is read. */
function preserveUnread<T extends { unread: boolean }>(
  current: T[],
  incoming: T[],
  key: keyof T,
): T[] {
  const unreadKeys = new Set(
    current.filter((item) => item.unread).map((item) => item[key]),
  );
  return incoming.map((item) =>
    unreadKeys.has(item[key]) && !item.unread
      ? { ...item, unread: true }
      : item);
}

function mergeChronicle(
  current: WorldChronicleEntry[],
  incoming: WorldChronicleEntry[],
): WorldChronicleEntry[] {
  const byId = new Map(current.map((entry) => [entry.id, entry]));
  for (const entry of incoming) {
    const existing = byId.get(entry.id);
    byId.set(entry.id, existing?.unread && !entry.unread
      ? { ...entry, unread: true }
      : entry);
  }
  return [...byId.values()]
    .sort((a, b) =>
      (a.world_minute ?? a.world_tick ?? 0)
      - (b.world_minute ?? b.world_tick ?? 0)
      || a.id.localeCompare(b.id))
    .slice(-MAX_WORLD_CHRONICLE);
}

function countWorldUnread(state: Pick<GameState, "rumors" | "worldChronicle" | "knownPeople">): number {
  return [
    ...state.rumors,
    ...state.worldChronicle,
    ...state.knownPeople,
  ].filter((entry) => entry.unread).length;
}

/** Reading one page of the World drawer clears only that page. Information on
 * the other pages keeps its quiet status-bar marker until the player actually
 * looks at it. This is local presentation state, not a quest acknowledgement. */
function markWorldTabRead(state: GameState, tab: WorldDrawerTab): GameState {
  const next = {
    ...state,
    rumors: tab === "rumors"
      ? state.rumors.map((rumor) => ({ ...rumor, unread: false }))
      : state.rumors,
    worldChronicle: tab === "chronicle"
      ? state.worldChronicle.map((entry) => ({ ...entry, unread: false }))
      : state.worldChronicle,
    knownPeople: tab === "people"
      ? state.knownPeople.map((npc) => ({ ...npc, unread: false }))
      : state.knownPeople,
  };
  return { ...next, worldUnread: countWorldUnread(next) };
}

function reduce(state: GameState, action: Action): GameState {
  switch (action.type) {
    case "logged_in":
      // Authentication is an identity boundary. Never let room state, nearby
      // names, dialogue, or transient event text from the previous account
      // survive a same-page login (including test harnesses and expired-token
      // recovery). Only the local audio preference belongs to the device.
      return {
        ...initialState,
        username: action.username,
        screen: "game",
        musicOn: state.musicOn,
      };
    case "logged_out":
      return { ...initialState, musicOn: state.musicOn };
    case "auth_failed":
      // Back to the front door, but keep local preferences (music).
      return { ...initialState, musicOn: state.musicOn, loginError: action.message };
    case "status":
      return action.status === "connected"
        ? { ...state, connection: action.status }
        : {
            ...state,
            connection: action.status,
            chestOpenPending: null,
            lootPendingCards: [],
            proximityOpenPending: null,
            shopPending: null,
            carriagePending: null,
            situationPending: false,
          };
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
      return state.lootPendingCards.length > 0
        ? state
        : { ...state, lootReveal: null };
    case "chest_open_pending":
      return { ...state, chestOpenPending: action.objectId };
    case "loot_card_pending":
      if (
        state.lootReveal?.objectId !== action.objectId
        || state.lootPendingCards.includes(action.cardIndex)
      ) return state;
      return {
        ...state,
        lootPendingCards: [...state.lootPendingCards, action.cardIndex],
      };
    case "proximity_open_pending":
      return {
        ...state,
        proximityOpenPending: {
          kind: action.kind,
          objectId: action.objectId,
        },
      };
    case "close_shop":
      return state.shopPending
        ? state
        : { ...state, shop: null };
    case "shop_pending":
      return { ...state, shopPending: action.pending };
    case "close_noticeboard":
      return { ...state, noticeboard: null };
    case "close_carriage":
      return state.carriagePending
        ? state
        : { ...state, carriage: null, carriagePending: null };
    case "carriage_pending":
      return { ...state, carriagePending: action.pending };
    case "close_situation":
      return state.situationPending
        ? state
        : { ...state, situation: null, situationPending: false };
    case "situation_pending":
      return { ...state, situationPending: true };
    case "dismiss_discovery":
      return state.discoveryToast?.id === action.id
        ? { ...state, discoveryToast: null }
        : state;
    case "open_world_drawer":
      return markWorldTabRead({
        ...state,
        worldDrawerOpen: true,
        worldDrawerTab: action.tab ?? state.worldDrawerTab,
      }, action.tab ?? state.worldDrawerTab);
    case "close_world_drawer":
      return { ...state, worldDrawerOpen: false };
    case "set_world_drawer_tab":
      return markWorldTabRead({ ...state, worldDrawerTab: action.tab }, action.tab);
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
      return {
        ...state,
        playerId: msg.player_id,
        username: msg.username,
        room: msg.state,
        chestOpenPending: null,
        lootPendingCards: [],
        proximityOpenPending: null,
        shopPending: null,
        carriagePending: null,
        situationPending: false,
      };
    case "state_update":
    case "room_changed": {
      const changedRoom = msg.type === "room_changed";
      const completedLocalTrade = msg.events.some((event) =>
        (
          event.event_type === "shop_purchased"
          || event.event_type === "item_sold"
        )
        && event.data.player_id === state.playerId
      );
      let next = {
        ...state,
        room: msg.state,
        // "Here & Now" belongs to this room. Preserve the transition events,
        // but do not carry an old district's transcript into the next.
        log: changedRoom
          ? appendLog([], formatEvents(msg.events, msg.state))
          : appendLog(state.log, formatEvents(msg.events, msg.state)),
        // A broadcast state means the round resolved (or the world moved on).
        actionLocked: false,
        waitingFor: [],
        // Travel invalidates every room- or proximity-bound surface.
        dialogue: changedRoom ? null : state.dialogue,
        inspection: changedRoom ? null : state.inspection,
        lootReveal: changedRoom ? null : state.lootReveal,
        chestOpenPending: changedRoom ? null : state.chestOpenPending,
        lootPendingCards: changedRoom ? [] : state.lootPendingCards,
        proximityOpenPending: changedRoom
          ? null
          : state.proximityOpenPending,
        shop: changedRoom ? null : state.shop,
        shopPending: changedRoom || completedLocalTrade
          ? null
          : state.shopPending,
        noticeboard: changedRoom ? null : state.noticeboard,
        carriage: changedRoom ? null : state.carriage,
        carriagePending: changedRoom ? null : state.carriagePending,
        situation: changedRoom ? null : state.situation,
        situationPending: changedRoom
          ? false
          : state.situationPending,
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
            chestOpenPending: null,
            lootPendingCards: [],
            proximityOpenPending: null,
            inspection: null,
            dialogue: null,
            shop: null,
            shopPending: null,
            noticeboard: null,
            carriage: null,
            carriagePending: null,
            situation: null,
            situationPending: false,
            worldDrawerOpen: false,
          };
        } else if (e.event_type === "chest_looted"
            && next.lootReveal
            && next.lootReveal.objectId === e.data.object_id) {
          const taken = e.data.item as ItemView;
          const taker = msg.state.players[String(e.data.player_id)]?.name ?? "someone";
          const finds = [...next.lootReveal.finds];
          const localTake = e.data.player_id === state.playerId;
          // Local pending card indices disambiguate identical items. Peer
          // events have no client request to correlate, so they consume the
          // first matching untaken reveal card.
          const pendingMatch = localTake
            ? next.lootPendingCards.find((cardIndex) => {
                const find = finds[cardIndex];
                return Boolean(
                  find
                  && !find.takenBy
                  && find.item.id === taken.id,
                );
              })
            : undefined;
          const idx = pendingMatch
            ?? finds.findIndex((find) =>
              !find.takenBy && find.item.id === taken.id
            );
          if (idx >= 0) finds[idx] = { ...finds[idx], takenBy: taker };
          next = {
            ...next,
            lootReveal: { ...next.lootReveal, finds },
            lootPendingCards: localTake && idx >= 0
              ? next.lootPendingCards.filter((cardIndex) => cardIndex !== idx)
              : next.lootPendingCards,
          };
        } else if (e.event_type === "shop_purchased"
            && next.shop
            && next.shop.id === e.data.shop_id) {
          next = {
            ...next,
            shop: {
              ...next.shop,
              stock: next.shop.stock.filter((entry) => entry.slot !== e.data.slot),
            },
          };
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
        chestOpenPending: null,
        lootPendingCards: [],
        proximityOpenPending: null,
        inspection: null,
        dialogue: null,
        shop: null,
        shopPending: null,
        noticeboard: null,
        carriage: null,
        carriagePending: null,
        situation: null,
        situationPending: false,
        worldDrawerOpen: false,
      };
    case "shop_opened":
      return {
        ...state,
        shop: msg.shop,
        shopPending: null,
        chestOpenPending: null,
        lootPendingCards: [],
        proximityOpenPending: null,
        inspection: null,
        dialogue: null,
        lootReveal: null,
        noticeboard: null,
        carriage: null,
        carriagePending: null,
        situation: null,
        situationPending: false,
        worldDrawerOpen: false,
      };
    case "shop_stock":
      if (!state.shop || state.shop.id !== msg.shop_id) return state;
      return {
        ...state,
        shop: { ...state.shop, stock: msg.stock },
        shopPending: state.shopPending === "buy"
          ? null
          : state.shopPending,
      };
    case "noticeboard_opened":
      return {
        ...state,
        noticeboard: msg.noticeboard,
        chestOpenPending: null,
        lootPendingCards: [],
        proximityOpenPending: null,
        inspection: null,
        dialogue: null,
        lootReveal: null,
        shop: null,
        shopPending: null,
        carriage: null,
        carriagePending: null,
        situation: null,
        situationPending: false,
        worldDrawerOpen: false,
      };
    case "carriage_opened":
      return {
        ...state,
        carriage: {
          object_id: msg.object_id,
          stop: {
            ...msg.stop,
            named_by: state.carriage?.stop.id === msg.stop.id
              ? state.carriage.stop.named_by
              : undefined,
          },
          destinations: msg.destinations,
          can_name: msg.can_name,
          name_limit: msg.name_limit,
          service: msg.service,
        },
        carriagePending: null,
        chestOpenPending: null,
        lootPendingCards: [],
        proximityOpenPending: null,
        inspection: null,
        dialogue: null,
        lootReveal: null,
        shop: null,
        shopPending: null,
        noticeboard: null,
        situation: null,
        situationPending: false,
        worldDrawerOpen: false,
      };
    case "carriage_stop_named": {
      if (!state.carriage) return state;
      return {
        ...state,
        carriage: {
          ...state.carriage,
          stop: state.carriage.stop.id === msg.stop_id
            ? {
                ...state.carriage.stop,
                name: msg.name,
                status: "operating",
                community_named: true,
                named_by: msg.named_by,
              }
            : state.carriage.stop,
          destinations: state.carriage.destinations.map((destination) =>
            destination.stop_id === msg.stop_id
              ? { ...destination, name: msg.name }
              : destination),
          can_name: state.carriage.stop.id === msg.stop_id
            ? false
            : state.carriage.can_name,
        },
        carriagePending: null,
      };
    }
    case "situation_opened":
      return {
        ...state,
        situation: msg.situation,
        situationPending: false,
        chestOpenPending: null,
        lootPendingCards: [],
        proximityOpenPending: null,
        inspection: null,
        dialogue: null,
        lootReveal: null,
        shop: null,
        shopPending: null,
        noticeboard: null,
        carriage: null,
        carriagePending: null,
        worldDrawerOpen: false,
      };
    case "situation_resolved":
      if (!state.situation || state.situation.id !== msg.situation_id) {
        return state;
      }
      return {
        ...state,
        situation: {
          ...state.situation,
          resolved: true,
          outcome: msg.outcome,
          result: msg.result,
          choices: [],
        },
        situationPending: false,
        log: appendLog(state.log, [{
          id: ++logSeq,
          kind: "discovery",
          text: msg.result,
        }]),
      };
    case "travel_started":
      return {
        ...state,
        // Keep the busy route surface mounted until room_changed installs the
        // destination. It is the visible progress state and blocks both
        // pointer and keyboard actions while the server advances world time
        // and hydrates a cold arrival room.
        carriagePending: "travel",
        log: appendLog(state.log, [{
          id: ++logSeq,
          kind: "ambient",
          text: `The carriage sets out for ${msg.destination_name}.`,
        }]),
      };
    case "carriage_arrived":
      {
        const total = msg.journey_minutes || msg.travel_minutes;
        const waited = msg.wait_minutes > 0
          ? `, including ${msg.wait_minutes} minutes waiting`
          : "";
        const danger = msg.danger > 0
          ? ` The road carried danger ${msg.danger}.`
          : "";
      return {
        ...state,
        carriage: null,
        carriagePending: null,
        log: appendLog(state.log, [{
          id: ++logSeq,
          kind: "ambient",
          text: `The carriage reaches ${msg.stop.name} after ${total} minutes${waited} (${msg.fare} coin${msg.fare === 1 ? "" : "s"}).${danger}`,
        }]),
      };
      }
    case "frontier_discovered":
      return {
        ...state,
        discoveryToast: {
          id: ++discoverySeq,
          name: msg.name,
          depth: msg.depth,
          biome: msg.biome,
          majorRegion: msg.major_region,
        },
        log: appendLog(state.log, [{
          id: ++logSeq,
          kind: "discovery",
          text: `${msg.name} emerges from the ${msg.biome}.`,
        }]),
      };
    case "world_sync":
      {
        const rumors = preserveUnread(state.rumors, msg.rumors, "id");
        const worldChronicle = mergeChronicle(state.worldChronicle, msg.chronicle);
        const knownPeople = preserveUnread(
          state.knownPeople,
          msg.known_people,
          "world_id",
        );
        const next: GameState = {
          ...state,
          worldTime: msg.time,
          rumors,
          worldChronicle,
          knownPeople,
          worldUnread: 0,
        };
        if (state.worldDrawerOpen) return markWorldTabRead(next, state.worldDrawerTab);
        return { ...next, worldUnread: countWorldUnread(next) };
      }
    case "world_time_updated":
      return { ...state, worldTime: msg.time };
    case "rumor_learned": {
      const rumor = state.worldDrawerOpen && state.worldDrawerTab === "rumors"
        ? { ...msg.rumor, unread: false }
        : msg.rumor;
      const next = {
        ...state,
        rumors: upsertById(state.rumors, rumor),
      };
      return { ...next, worldUnread: countWorldUnread(next) };
    }
    case "chronicle_added": {
      const entry = state.worldDrawerOpen && state.worldDrawerTab === "chronicle"
        ? { ...msg.entry, unread: false }
        : msg.entry;
      const next = {
        ...state,
        worldChronicle: mergeChronicle(state.worldChronicle, [entry]),
      };
      return { ...next, worldUnread: countWorldUnread(next) };
    }
    case "known_npc_updated": {
      const npc = state.worldDrawerOpen && state.worldDrawerTab === "people"
        ? { ...msg.npc, unread: false }
        : msg.npc;
      const next = {
        ...state,
        knownPeople: upsertById(state.knownPeople, npc, "world_id"),
      };
      return { ...next, worldUnread: countWorldUnread(next) };
    }
    case "error":
      return {
        ...state,
        log: appendLog(state.log, [{ id: ++logSeq, kind: "error", text: msg.message }]),
        dialogue: state.dialogue ? { ...state.dialogue, pending: false } : null,
        chestOpenPending: null,
        lootPendingCards: [],
        proximityOpenPending: null,
        shopPending: null,
        carriagePending: null,
        situationPending: false,
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
  openShop(objectId: string): void;
  buyShopItem(objectId: string, slot: number, itemId: number, stockedOn: string): void;
  sellShopItem(objectId: string, slot: number, itemId: number): void;
  openNoticeboard(objectId: string): void;
  postNotice(objectId: string, body: string): void;
  deleteNotice(objectId: string, noticeId: number): void;
  openCarriage(objectId: string): void;
  closeCarriage(): void;
  nameCarriageStop(name: string): void;
  travelByCarriage(destinationId: number): void;
  openSituation(objectId: string): void;
  resolveSituation(choiceId: string): void;
  closeSituation(): void;
  dismissDiscovery(id: number): void;
  /** Take one chosen item from an opened chest (the popup's Take button).
   * `index` is the item's current position among the chest's leftovers;
   * `cardIndex` is its stable position in the open reveal. */
  takeItem(
    objectId: string,
    index: number,
    itemId: number,
    cardIndex: number,
  ): void;
  /** Hold/put away a belt slot (toggle). Pass null to empty your hands. */
  selectSlot(index: number | null): void;
  inspect(objectId: string): void;
  openDialogue(npc: NpcState): void;
  closeDialogue(): void;
  closeInspection(): void;
  /** Dismiss the chest-reveal popup. */
  closeLoot(): void;
  closeShop(): void;
  closeNoticeboard(): void;
  openWorldDrawer(tab?: WorldDrawerTab): void;
  closeWorldDrawer(): void;
  setWorldDrawerTab(tab: WorldDrawerTab): void;
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
  const chestOpenPendingRef = useRef<string | null>(null);
  const lootCardPendingRef = useRef<Set<string>>(new Set());
  const proximityOpenPendingRef = useRef<string | null>(null);
  const shopTradePendingRef = useRef(false);
  stateRef.current = state;

  useEffect(() => () => socketRef.current?.close(), []);

  const api = useMemo<GameApi>(() => {
    const clearRequestRefs = () => {
      chestOpenPendingRef.current = null;
      lootCardPendingRef.current.clear();
      proximityOpenPendingRef.current = null;
      shopTradePendingRef.current = false;
    };

    const handleMessage = (msg: ServerMessage) => {
      if (msg.type === "error" || msg.type === "room_changed") {
        clearRequestRefs();
      } else if (msg.type === "chest_contents") {
        if (chestOpenPendingRef.current === msg.object_id) {
          chestOpenPendingRef.current = null;
        }
      } else if (
        msg.type === "shop_opened"
        || msg.type === "noticeboard_opened"
        || msg.type === "carriage_opened"
        || msg.type === "situation_opened"
      ) {
        proximityOpenPendingRef.current = null;
      } else if (msg.type === "shop_stock") {
        shopTradePendingRef.current = false;
      } else if (msg.type === "state_update") {
        const current = stateRef.current;
        const finds = current.lootReveal?.finds.map((find) => ({ ...find }));
        for (const event of msg.events) {
          if (
            event.event_type === "chest_opened"
            && event.data.player_id === current.playerId
            && chestOpenPendingRef.current === String(event.data.object_id)
          ) {
            chestOpenPendingRef.current = null;
          } else if (
            event.event_type === "chest_looted"
            && event.data.player_id === current.playerId
            && current.lootReveal
            && current.lootReveal.objectId === event.data.object_id
            && finds
          ) {
            const item = event.data.item as ItemView;
            const objectId = current.lootReveal.objectId;
            // Match the acknowledgement against the synchronous request
            // keys, not merely the first identical untaken item in the last
            // rendered state. Several take-all acknowledgements can arrive
            // before React publishes a render; the Set still records which
            // duplicate card each outstanding request belongs to.
            const cardIndex = finds.findIndex((find, index) =>
              !find.takenBy
              && find.item.id === item.id
              && lootCardPendingRef.current.has(`${objectId}:${index}`)
            );
            if (cardIndex >= 0) {
              lootCardPendingRef.current.delete(
                `${objectId}:${cardIndex}`,
              );
              finds[cardIndex] = { ...finds[cardIndex], takenBy: "response" };
            } else {
              // A tightly batched peer update can make stateRef one render
              // behind the reducer. The durable pending state still guards
              // every unresolved card; discard only the synchronous shim.
              lootCardPendingRef.current.clear();
            }
          }
          if (
            (
              event.event_type === "shop_purchased"
              || event.event_type === "item_sold"
            )
            && event.data.player_id === current.playerId
          ) {
            shopTradePendingRef.current = false;
          }
        }
      }

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
        socketRef.current.connect(handleMessage, (status) => {
          if (status !== "connected") clearRequestRefs();
          dispatch({ type: "status", status });
        });
      }
      return socketRef.current;
    };

    const joinAs = (username: string, token: string) => {
      clearRequestRefs();
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
        clearRequestRefs();
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(NAME_KEY);
        socketRef.current?.close();
        socketRef.current = null;
        dispatch({ type: "logged_out" });
        location.reload();
      },
      rejoin() {
        const token = localStorage.getItem(TOKEN_KEY);
        if (!token) return;
        clearRequestRefs();
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
        if (
          chestOpenPendingRef.current
          || proximityOpenPendingRef.current
          || stateRef.current.chestOpenPending
          || stateRef.current.proximityOpenPending
        ) return;
        chestOpenPendingRef.current = objectId;
        dispatch({ type: "chest_open_pending", objectId });
        socket().send({ type: "open_chest", object_id: objectId });
      },
      openShop(objectId) {
        if (
          chestOpenPendingRef.current
          || proximityOpenPendingRef.current
          || stateRef.current.chestOpenPending
          || stateRef.current.proximityOpenPending
        ) return;
        proximityOpenPendingRef.current = `shop:${objectId}`;
        dispatch({
          type: "proximity_open_pending",
          kind: "shop",
          objectId,
        });
        socket().send({ type: "open_shop", object_id: objectId });
      },
      buyShopItem(objectId, slot, itemId, stockedOn) {
        if (shopTradePendingRef.current || stateRef.current.shopPending) return;
        shopTradePendingRef.current = true;
        dispatch({ type: "shop_pending", pending: "buy" });
        socket().send({
          type: "buy_shop_item",
          object_id: objectId,
          slot,
          item_id: itemId,
          stocked_on: stockedOn,
        });
      },
      sellShopItem(objectId, slot, itemId) {
        if (shopTradePendingRef.current || stateRef.current.shopPending) return;
        shopTradePendingRef.current = true;
        dispatch({ type: "shop_pending", pending: "sell" });
        socket().send({
          type: "sell_shop_item",
          object_id: objectId,
          slot,
          item_id: itemId,
        });
      },
      openNoticeboard(objectId) {
        if (
          chestOpenPendingRef.current
          || proximityOpenPendingRef.current
          || stateRef.current.chestOpenPending
          || stateRef.current.proximityOpenPending
        ) return;
        proximityOpenPendingRef.current = `noticeboard:${objectId}`;
        dispatch({
          type: "proximity_open_pending",
          kind: "noticeboard",
          objectId,
        });
        socket().send({ type: "open_noticeboard", object_id: objectId });
      },
      postNotice(objectId, body) {
        socket().send({ type: "post_notice", object_id: objectId, body });
      },
      deleteNotice(objectId, noticeId) {
        socket().send({ type: "delete_notice", object_id: objectId, notice_id: noticeId });
      },
      openCarriage(objectId) {
        if (
          chestOpenPendingRef.current
          || proximityOpenPendingRef.current
          || stateRef.current.chestOpenPending
          || stateRef.current.proximityOpenPending
        ) return;
        proximityOpenPendingRef.current = `carriage:${objectId}`;
        dispatch({
          type: "proximity_open_pending",
          kind: "carriage",
          objectId,
        });
        socket().send({ type: "open_carriage", object_id: objectId });
      },
      closeCarriage() {
        dispatch({ type: "close_carriage" });
      },
      nameCarriageStop(name) {
        const carriage = stateRef.current.carriage;
        if (!carriage || stateRef.current.carriagePending || !name.trim()) return;
        dispatch({ type: "carriage_pending", pending: "name" });
        socket().send({
          type: "name_carriage_stop",
          object_id: carriage.object_id,
          name: name.trim(),
        });
      },
      travelByCarriage(destinationId) {
        const carriage = stateRef.current.carriage;
        if (!carriage || stateRef.current.carriagePending) return;
        const destination = carriage.destinations.find((stop) => stop.stop_id === destinationId);
        if (!destination || !destination.available_now) return;
        dispatch({ type: "carriage_pending", pending: "travel" });
        socket().send({
          type: "travel_by_carriage",
          object_id: carriage.object_id,
          stop_id: destinationId,
        });
      },
      openSituation(objectId) {
        if (
          chestOpenPendingRef.current
          || proximityOpenPendingRef.current
          || stateRef.current.chestOpenPending
          || stateRef.current.proximityOpenPending
        ) return;
        proximityOpenPendingRef.current = `situation:${objectId}`;
        dispatch({
          type: "proximity_open_pending",
          kind: "situation",
          objectId,
        });
        socket().send({ type: "open_situation", object_id: objectId });
      },
      resolveSituation(choiceId) {
        const situation = stateRef.current.situation;
        if (!situation || situation.resolved || stateRef.current.situationPending) return;
        const choice = situation.choices.find((candidate) => candidate.id === choiceId);
        if (!choice) return;
        dispatch({ type: "situation_pending" });
        socket().send({
          type: "resolve_situation",
          object_id: situation.object_id,
          choice_id: choiceId,
        });
      },
      closeSituation() {
        dispatch({ type: "close_situation" });
      },
      dismissDiscovery(id) {
        dispatch({ type: "dismiss_discovery", id });
      },
      takeItem(objectId, index, itemId, cardIndex) {
        const requestKey = `${objectId}:${cardIndex}`;
        if (
          lootCardPendingRef.current.has(requestKey)
          || stateRef.current.lootPendingCards.includes(cardIndex)
        ) return;
        lootCardPendingRef.current.add(requestKey);
        dispatch({ type: "loot_card_pending", objectId, cardIndex });
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
      closeShop() {
        dispatch({ type: "close_shop" });
      },
      closeNoticeboard() {
        dispatch({ type: "close_noticeboard" });
      },
      openWorldDrawer(tab) {
        dispatch({ type: "open_world_drawer", tab });
      },
      closeWorldDrawer() {
        dispatch({ type: "close_world_drawer" });
      },
      setWorldDrawerTab(tab) {
        dispatch({ type: "set_world_drawer_tab", tab });
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
