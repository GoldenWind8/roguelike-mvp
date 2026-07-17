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
}

export interface ObjectDetail extends ObjectSummary {
  description: string;
  details: string[];
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
  | { type: "npc_dialogue"; npc_id: string; name: string; player_text: string; text: string }
  | { type: "world_reset" }
  | { type: "error"; message: string; code?: string };

// --- client -> server -------------------------------------------------------

export type ClientAction =
  | { type: "action"; action_type: "move"; direction: [number, number] }
  | { type: "action"; action_type: "attack"; target_id: string }
  | { type: "action"; action_type: "wait" }
  | { type: "action"; action_type: "bomb"; target_tile: [number, number] };

export type ClientMessage =
  | { type: "join"; token: string }
  | ClientAction
  | { type: "talk"; npc_id: string; text: string }
  | { type: "inspect_object"; object_id: string }
  | MockOnlyMessage;

// --- mock-only extensions ---------------------------------------------------
// Inventory has no backend contract yet (it is on the roadmap's Later list).
// The mockup fakes item use through this message so the UI can be designed
// now; when inventory lands server-side, this becomes a real action shape.

export type MockOnlyMessage = { type: "mock_use_item"; item_id: string; heal: number };

export type ConnectionStatus = "disconnected" | "connecting" | "connected";
