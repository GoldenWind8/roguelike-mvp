import type { ClientMessage, ConnectionStatus, ServerMessage } from "./types";

/**
 * The one seam between the UI and the world: a thin WebSocket adapter
 * implements it, and nothing above this interface may care how messages
 * travel.
 */
export interface GameSocket {
  connect(
    onMessage: (msg: ServerMessage) => void,
    onStatus: (status: ConnectionStatus) => void,
  ): void;
  send(msg: ClientMessage): void;
  close(): void;
}
