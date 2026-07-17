import type { ClientMessage, ConnectionStatus, ServerMessage } from "./types";

/**
 * The one seam between the UI and the world. The mockup binds MockGameSocket;
 * integration binds a thin WebSocket adapter with the same interface. Nothing
 * above this interface may care which one it is talking to.
 */
export interface GameSocket {
  connect(
    onMessage: (msg: ServerMessage) => void,
    onStatus: (status: ConnectionStatus) => void,
  ): void;
  send(msg: ClientMessage): void;
  close(): void;
}
