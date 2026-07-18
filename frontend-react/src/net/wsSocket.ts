/**
 * A thin WebSocket adapter behind the GameSocket seam. Outgoing messages
 * queue until the socket opens, so callers can connect-and-send in one
 * breath.
 */
import type { GameSocket } from "./socket";
import type { ClientMessage, ConnectionStatus, ServerMessage } from "./types";

export class RealGameSocket implements GameSocket {
  private ws: WebSocket | null = null;
  private queue: ClientMessage[] = [];

  connect(
    onMessage: (msg: ServerMessage) => void,
    onStatus: (status: ConnectionStatus) => void,
  ): void {
    onStatus("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws = ws;
    ws.onopen = () => {
      onStatus("connected");
      for (const msg of this.queue.splice(0)) ws.send(JSON.stringify(msg));
    };
    ws.onclose = () => onStatus("disconnected");
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data) as ServerMessage);
      } catch {
        // A frame that isn't JSON isn't ours to crash on.
      }
    };
  }

  send(msg: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else this.queue.push(msg);
  }

  close(): void {
    if (this.ws) {
      // Neuter the handlers first: a deliberate close (logout, death rejoin)
      // must not flash "disconnected" over the next socket's status.
      this.ws.onopen = this.ws.onclose = this.ws.onmessage = null;
      this.ws.close();
    }
    this.ws = null;
    this.queue = [];
  }
}
