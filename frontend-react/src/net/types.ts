/**
 * Wire types mirroring the real backend contract, field for field:
 *
 *   - Actor shape:       backend/entities.py   Actor.to_dict / NPC.to_dict
 *   - Room state shape:  backend/room_state.py RoomState.to_dict
 *                        + backend/room_engine.py get_state (mode/started/phase)
 *   - Object shapes:     backend/room_loader.py to_summary_dict / to_dict
 *   - Messages:          backend/main.py websocket handlers
 *
 * The mock socket and (later) the real socket both speak exactly these
 * shapes, so binding the UI to the live server is a socket swap, not a
 * refactor. Anything that exists ONLY in the mockup lives at the bottom
 * under "mock-only extensions" so the integration debt is visible.
 */

export type Disposition = "hostile" | "neutral" | "friendly";
export type RoomMode = "combat" | "exploration";

// --- the loot system (backend/items.py, docs/LOOT.md) ------------------------

export type Rarity = "common" | "rare" | "legendary";
export type ItemType = "wearable" | "consumable" | "throwable" | "weapon";

/** Typed art reference: emoji today, image-gen URLs tomorrow — render by kind. */
export interface ItemArt {
  kind: "emoji" | "url";
  value: string;
}

/** One effect atom from the item's validated payload (items.py vocabulary). */
export interface EffectAtom {
  kind: "stat_mod" | "restore_hp" | "restore_hunger" | "damage";
  stat?: "attack_damage" | "defense" | "max_hp";
  amount: number;
  duration_s?: number;
}

export interface ItemPayload {
  effects?: EffectAtom[];
  /** weapon */
  damage?: number;
  range?: number;
  /** throwable */
  throw_range?: number;
  area?: { shape: "radius"; size: number };
}

/** items.item_view — the denormalized snapshot that rides in packs/chests. */
export interface ItemView {
  id: number;
  name: string;
  description: string;
  rarity: Rarity;
  type: ItemType;
  art: ItemArt;
  payload: ItemPayload;
  origin: "seed" | "llm";
}

/** One pack slot (backend/inventory.py). */
export interface InventorySlot {
  item: ItemView;
  quantity: number;
  equipped: boolean;
}

/** A ticking buff/debuff (world-clock scoped; server does the remaining math). */
export interface ActiveEffect {
  stat: string;
  amount: number;
  source: string;
  remaining_s: number;
}

export interface ActorState {
  id: string;
  name: string;
  position: [number, number];
  hp: number;
  max_hp: number;
  defense: number;
  attack_damage: number;
  is_alive: boolean;
  disposition: Disposition;
  /** Always sent by the real server; optional so mock literals stay lean. */
  active_effects?: ActiveEffect[];
  /** Players only (their 10-slot pack). */
  inventory?: InventorySlot[];
  /** Players only: the hunger meter (0..max_hunger, LOOT.md Decision 5). */
  hunger?: number;
  max_hunger?: number;
}

/** One find inside a chest_opened event / chest_contents reply: the item
 * (still in the chest — taking is the player's choice via take_item) and
 * whether the LLM minted it just now (fanfare). */
export interface ChestFind {
  item: ItemView;
  minted: boolean;
}

export interface NpcState extends ActorState {
  role: string;
  party_owner_id: string | null;
}

export interface ObjectSummary {
  id: string;
  type: string;
  position: [number, number];
  label: string;
  /** Chest lifecycle (rolled-at-open, docs/LOOT.md). */
  opened?: boolean;
  contents_count?: number;
}

export interface ObjectDetail extends ObjectSummary {
  description: string;
  details: string[];
  /** Items waiting in an opened chest for anyone with pack room. */
  contents?: ItemView[];
}

export interface GameEvent {
  event_type: string;
  data: Record<string, unknown>;
  round: number;
}

export interface RoomStatePayload {
  room: {
    /** An int from the DB on the real server; the mock uses a slug. */
    id: number | string;
    name: string;
    width: number;
    height: number;
    mode: RoomMode; // live derived mode, injected by the engine (M7)
  };
  round: number;
  grid: (string | null)[][];
  walls: [number, number][];
  objects: ObjectSummary[];
  players: Record<string, ActorState>;
  enemies: Record<string, ActorState>;
  npcs: Record<string, NpcState>;
  pending_player_ids: string[];
  started: boolean;
  phase: string;
}

// --- server -> client -------------------------------------------------------

export type ServerMessage =
  | { type: "join_ack"; player_id: string; username: string; state: RoomStatePayload }
  | { type: "state_update"; state: RoomStatePayload; events: GameEvent[] }
  | { type: "room_changed"; state: RoomStatePayload; events: GameEvent[] }
  | { type: "action_locked" }
  | { type: "waiting_for"; player_ids: string[] }
  | { type: "object_inspection"; object: ObjectDetail }
  // What still waits in an already-opened chest (1:1 reply to open_chest —
  // looking inside is not a world-visible act). Renders the selection popup.
  | { type: "chest_contents"; object_id: string; items: ChestFind[] }
  | { type: "npc_dialogue"; npc_id: string; name: string; player_text: string; text: string }
  | { type: "world_reset" }
  | { type: "error"; message: string; code?: string };

// --- client -> server -------------------------------------------------------

export type ClientAction =
  | { type: "action"; action_type: "move"; direction: [number, number] }
  | { type: "action"; action_type: "attack"; target_id: string }
  | { type: "action"; action_type: "wait" }
  | { type: "action"; action_type: "consume"; slot: number }
  | { type: "action"; action_type: "throw"; slot: number; target_tile: [number, number] };

export type ClientMessage =
  | { type: "join"; token: string }
  | ClientAction
  | { type: "talk"; npc_id: string; text: string }
  | { type: "inspect_object"; object_id: string }
  // Loot requests outside the action economy (backend/main.py): opening a
  // chest (roll or claim — same message), and gear fiddling.
  | { type: "open_chest"; object_id: string }
  // Take ONE chosen item from an opened chest. `index` is the item's current
  // position in the chest; `item_id` guards against the contents shifting
  // under a stale click (the server refuses rather than grab the wrong thing).
  | { type: "take_item"; object_id: string; index: number; item_id: number }
  | { type: "equip"; slot: number }
  | { type: "unequip"; slot: number };

export type ConnectionStatus = "disconnected" | "connecting" | "connected";
