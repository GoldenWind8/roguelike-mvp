/**
 * Wire types mirroring the real backend contract, field for field:
 *
 *   - Actor shape:       backend/entities.py   Actor.to_dict / NPC.to_dict
 *   - Room state shape:  backend/room_state.py RoomState.to_dict
 *                        + backend/room_engine.py get_state (mode/started/phase)
 *   - Object shapes:     backend/room_loader.py to_summary_dict / to_dict
 *   - Messages:          backend/main.py websocket handlers
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
  active_effects?: ActiveEffect[];
  /** Accepted server-owned presentation; null keeps the compact fallback. */
  image: string | null;
  visual_size: [number, number];
  /** Players only (their 10-slot pack). */
  inventory?: InventorySlot[];
  /** Players only: the hunger meter (0..max_hunger, LOOT.md Decision 5). */
  hunger?: number;
  max_hunger?: number;
  /** Players only: persistent currency balance. */
  coins?: number;
}

/** One find inside a chest_opened event / chest_contents reply: the item
 * (still in the chest — taking is the player's choice via take_item) and
 * whether the LLM minted it just now (fanfare). */
export interface ChestFind {
  item: ItemView;
  minted: boolean;
}

export interface ShopStock {
  slot: number;
  item: ItemView;
  price: number;
  minted: boolean;
  stocked_on: string;
}

export interface ShopView {
  id: string;
  object_id: string;
  label: string;
  stock: ShopStock[];
  restocks_at: string;
}

export interface NoticeView {
  id: string;
  kind: "authored" | "player";
  author: string;
  body: string;
  posted_at: string | null;
  expires_at: string | null;
  can_delete: boolean;
}

export interface NoticeboardView {
  id: string;
  object_id: string;
  label: string;
  notices: NoticeView[];
  text_limit: number;
  post_ttl_days: number;
  max_player_posts: number;
}

// --- shared carriage network ---------------------------------------------

/** A carriage stop the player has actually discovered. Stop names are shared
 * world state and may have been supplied by another player. */
export interface CarriageStopView {
  id: number;
  name: string;
  room_id: number;
  biome: string;
  status: string;
  community_named: boolean;
  named_by?: string;
}

/** A public route from the carriage currently beside the player. The server
 * omits unnamed generated stops entirely; the UI never silhouettes them. */
export interface CarriageDestinationView {
  stop_id: number;
  name: string;
  room_id: number;
  travel_minutes: number;
  fare: number;
  route_stop_ids: number[];
  wait_minutes: number;
  transfer_wait_minutes: number;
  journey_minutes: number;
  next_departure_minute: number | null;
  next_departure_minute_of_day: number | null;
  boarding_minute: number | null;
  arrival_minute: number | null;
  available_now: boolean;
  boarding_grace_minutes: number;
  route_status: "operating" | "delayed" | "dangerous" | "mixed";
  route_statuses: string[];
  danger: number;
  max_leg_danger: number;
  route_ids: number[];
  leg_departure_minutes: number[];
  leg_arrival_minutes: number[];
}

export interface CarriageServiceView {
  world_minute: number;
  minute_of_day: number;
  status: "boarding" | "waiting" | "unavailable";
  next_departure_minute: number | null;
  wait_minutes: number | null;
}

export interface CarriageView {
  object_id: string;
  stop: CarriageStopView;
  destinations: CarriageDestinationView[];
  can_name: boolean;
  name_limit: number;
  service: CarriageServiceView;
}

// --- evidence-gated authored situations ----------------------------------

export interface SituationChoiceView {
  id: string;
  label: string;
  description: string;
}

/** A consequence-bearing interaction, never a quest or tracked objective.
 * The server omits choices whose evidence requirements are not yet known. */
export interface SituationView {
  id: string;
  object_id: string;
  title: string;
  kicker: string;
  description: string;
  resolved: boolean;
  outcome: string | null;
  result: string | null;
  choices: SituationChoiceView[];
}

// --- player-known living-world context ------------------------------------
//
// These views are deliberately evidence-shaped. They may describe only what
// this player has witnessed, heard, or learned; private NPC plans and hidden
// simulation state never belong in this contract.

export type WorldPhase = "deep_night" | "dawn" | "morning" | "afternoon" | "dusk" | "night";

export interface WorldTimeView {
  /** Canonical persistent clock used by the living-world simulator. */
  world_minute: number;
  /** Transitional compatibility with the first UI-only contract draft. */
  tick?: number;
  day: number;
  phase: WorldPhase;
  /** Server-authored display text, such as "Day 3, dusk". */
  label: string;
}

export type NpcActivityKind =
  | "idle"
  | "working"
  | "travelling"
  | "resting"
  | "talking"
  | "fighting"
  | "unknown";

export interface NpcActivityView {
  kind: NpcActivityKind;
  /** An observable description only: "tending the orchard", not a hidden plan. */
  label: string;
  interruptible?: boolean;
}

export type KnowledgeProvenance = "witnessed" | "heard" | "found";

/** An untracked clue or report. Rumors have no objectives, completion state,
 * or implied promise that they lead to a quest. */
export interface RumorView {
  id: string;
  title: string;
  body: string;
  provenance: KnowledgeProvenance;
  learned_at: string;
  source?: string | null;
  place?: string | null;
  related_npc_ids?: string[];
  unread: boolean;
}

export interface WorldChronicleEntry {
  id: string;
  world_minute: number;
  /** Transitional compatibility with the first UI-only contract draft. */
  world_tick?: number;
  happened_at: string;
  title: string;
  body: string;
  provenance: KnowledgeProvenance;
  place?: string | null;
  actor_world_ids?: string[];
  /** True when included in the bounded catch-up since this player's last visit. */
  while_away: boolean;
  unread: boolean;
}

export type KnownNpcAvailability = "present" | "travelling" | "away" | "unknown" | "dead";
export type KnownNpcCondition = "well" | "wounded" | "critical" | "dead" | "unknown";
export type RelationshipTone =
  | "hostile"
  | "wary"
  | "unfamiliar"
  | "cordial"
  | "trusting"
  | "devoted";

/** A safe, player-facing conversation opening. The prompt is still ordinary
 * player speech; selecting it never invokes a quest or scripted objective. */
export interface DialogueTopicView {
  id: string;
  label: string;
  prompt: string;
}

export interface KnownNpcView {
  world_id: string;
  name: string;
  role: string;
  image: string | null;
  relationship: RelationshipTone;
  relationship_note?: string | null;
  availability: KnownNpcAvailability;
  /** Last condition the player personally observed; never live off-screen state. */
  condition?: {
    kind: KnownNpcCondition;
    label: string;
  };
  activity?: NpcActivityView;
  last_seen?: {
    room_name: string;
    at: string;
    note?: string;
  };
  dialogue_topics?: DialogueTopicView[];
  unread: boolean;
}

export interface NpcState extends ActorState {
  role: string;
  party_owner_id: string | null;
  /** Stable authored identity and observable activity arrive with the living
   * world protocol. Optional while older servers provide the compact shape. */
  world_id?: string;
  activity?: NpcActivityView;
  dialogue_topics?: DialogueTopicView[];
}

export interface ObjectSummary {
  id: string;
  type: string;
  /** Definition origin. The server expands collision into absolute cells. */
  position: [number, number];
  label: string;
  occupied_cells: [number, number][];
  blocks_movement: boolean;
  /** Optional isolated world sprite; null keeps the compact fallback icon. */
  image: string | null;
  /** Presentation size in grid cells, independent of logical collision. */
  visual_size: [number, number];
  /** Chest lifecycle (rolled-at-open, docs/LOOT.md). */
  opened?: boolean;
  contents_count?: number;
  /** Generic exploration interaction selected by authored object data. */
  interaction?: "shop" | "noticeboard" | "carriage" | "situation";
}

export interface ObjectDetail extends ObjectSummary {
  description: string;
  details: string[];
  /** Items waiting in an opened chest for anyone with pack room. */
  contents?: ItemView[];
}

export interface ExitSummary {
  position: [number, number];
  to_room_id: number;
  label: string;
}

export interface FrontierExitSummary {
  position: [number, number];
  label: string;
}

export interface GameEvent {
  event_type: string;
  data: Record<string, unknown>;
  round: number;
}

export interface RoomStatePayload {
  room: {
    id: number;
    name: string;
    width: number;
    height: number;
    mode: RoomMode; // live derived mode, injected by the engine (M7)
  };
  round: number;
  grid: (string | null)[][];
  walls: [number, number][];
  /** Connected door/portal presentation; traversal remains server-owned. */
  exits: ExitSummary[];
  /** Procedural border crossings. Destination truth is private until entered. */
  frontier_exits?: FrontierExitSummary[];
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
  // Player-private living-world knowledge. These messages must never contain
  // NPC secrets, unrevealed plans, or omniscient locations.
  | {
      type: "world_sync";
      time: WorldTimeView;
      rumors: RumorView[];
      chronicle: WorldChronicleEntry[];
      known_people: KnownNpcView[];
    }
  | { type: "world_time_updated"; time: WorldTimeView }
  | { type: "rumor_learned"; rumor: RumorView }
  | { type: "chronicle_added"; entry: WorldChronicleEntry }
  | { type: "known_npc_updated"; npc: KnownNpcView }
  | { type: "action_locked" }
  | { type: "waiting_for"; player_ids: string[] }
  | { type: "object_inspection"; object: ObjectDetail }
  // What still waits in an already-opened chest (1:1 reply to open_chest —
  // looking inside is not a world-visible act). Renders the selection popup.
  | { type: "chest_contents"; object_id: string; items: ChestFind[] }
  | { type: "shop_opened"; shop: ShopView }
  | { type: "shop_stock"; shop_id: string; stock: ShopStock[] }
  | { type: "noticeboard_opened"; noticeboard: NoticeboardView }
  | {
      type: "carriage_opened";
      object_id: string;
      stop: CarriageStopView;
      destinations: CarriageDestinationView[];
      can_name: boolean;
      name_limit: number;
      service: CarriageServiceView;
    }
  | { type: "carriage_stop_named"; stop_id: number; name: string; named_by?: string }
  | { type: "travel_started"; stop_id: number; destination_name: string }
  | {
      type: "carriage_arrived";
      stop: CarriageStopView;
      travel_minutes: number;
      journey_minutes: number;
      wait_minutes: number;
      route_status: string;
      danger: number;
      fare: number;
    }
  | { type: "situation_opened"; situation: SituationView }
  | {
      type: "situation_resolved";
      situation_id: string;
      outcome: string;
      result: string;
    }
  | {
      type: "frontier_discovered";
      name: string;
      depth: number;
      biome: string;
      major_region: string;
    }
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
  | { type: "open_shop"; object_id: string }
  | { type: "buy_shop_item"; object_id: string; slot: number; item_id: number; stocked_on: string }
  | { type: "open_noticeboard"; object_id: string }
  | { type: "post_notice"; object_id: string; body: string }
  | { type: "delete_notice"; object_id: string; notice_id: number }
  | { type: "open_carriage"; object_id: string }
  | { type: "name_carriage_stop"; object_id: string; name: string }
  | { type: "travel_by_carriage"; object_id: string; stop_id: number }
  | { type: "open_situation"; object_id: string }
  | { type: "resolve_situation"; object_id: string; choice_id: string }
  | { type: "equip"; slot: number }
  | { type: "unequip"; slot: number };

export type ConnectionStatus = "disconnected" | "connecting" | "connected";
