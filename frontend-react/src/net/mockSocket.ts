/**
 * A stand-in server that speaks the real wire protocol. It owns the mock
 * world, applies the same rules the backend would (server-authoritative:
 * the UI never mutates state directly), and emits the same messages.
 *
 * Deliberate simplifications vs. the real engine, so the mock stays a
 * mock: combat resolves instantly after your action (no simultaneous-turn
 * collection, so no action_locked / waiting_for traffic), and death is a
 * 5-second nap by the hearth instead of a real recovery loop.
 */
import type { GameSocket } from "./socket";
import type {
  ActorState,
  ClientMessage,
  ConnectionStatus,
  GameEvent,
  ServerMessage,
} from "./types";
import {
  AMBIENT_LINES,
  buildWorld,
  deriveMode,
  key,
  makeEvent,
  npcReply,
  snapshot,
  type MockWorld,
} from "../mock/world";

const chebyshev = (a: [number, number], b: [number, number]) =>
  Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]));

// Targeting (attack, talk) uses the server's rule: orthogonally adjacent.
const manhattan = (a: [number, number], b: [number, number]) =>
  Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);

/** Which real event family an actor's death/attack belongs to, from the
 * mock's id convention ("player-", "enemy-", "npc-"). */
const kindOf = (a: ActorState): "player" | "enemy" | "npc" =>
  a.id.startsWith("enemy-") ? "enemy" : a.id.startsWith("npc-") ? "npc" : "player";

export class MockGameSocket implements GameSocket {
  private world: MockWorld = buildWorld();
  private onMessage: ((msg: ServerMessage) => void) | null = null;
  private meId: string | null = null;
  private timers: number[] = [];
  private ratCount = 0;

  connect(onMessage: (msg: ServerMessage) => void, onStatus: (s: ConnectionStatus) => void): void {
    this.onMessage = onMessage;
    onStatus("connecting");
    this.timers.push(window.setTimeout(() => onStatus("connected"), 350));
  }

  close(): void {
    this.timers.forEach((t) => window.clearTimeout(t));
    this.timers = [];
    this.onMessage = null;
  }

  send(msg: ClientMessage): void {
    switch (msg.type) {
      case "join":
        this.handleJoin(msg.token);
        break;
      case "action":
        this.handleAction(msg);
        break;
      case "talk":
        this.handleTalk(msg.npc_id, msg.text);
        break;
      case "inspect_object":
        this.handleInspect(msg.object_id);
        break;
      case "mock_use_item":
        this.handleUseItem(msg.item_id, msg.heal);
        break;
    }
  }

  // --- helpers ---------------------------------------------------------------

  private emit(msg: ServerMessage) {
    this.onMessage?.(msg);
  }

  private emitState(events: GameEvent[]) {
    this.emit({ type: "state_update", state: snapshot(this.world), events });
  }

  private me(): ActorState | null {
    return this.meId ? this.world.players.get(this.meId) ?? null : null;
  }

  private event(type: string, data: Record<string, unknown> = {}): GameEvent {
    return makeEvent(type, data, this.world.round);
  }

  private blocked(x: number, y: number): boolean {
    const { width, height } = this.world.room;
    if (x < 0 || y < 0 || x >= width || y >= height) return true;
    if (this.world.walls.has(key(x, y))) return true;
    if (this.world.objects.some((o) => o.position[0] === x && o.position[1] === y)) return true;
    const occupied = (a: ActorState) => a.is_alive && a.position[0] === x && a.position[1] === y;
    for (const p of this.world.players.values()) if (occupied(p)) return true;
    for (const e of this.world.enemies.values()) if (occupied(e)) return true;
    for (const n of this.world.npcs.values()) if (occupied(n)) return true;
    return false;
  }

  private findActor(id: string): ActorState | undefined {
    return this.world.players.get(id) ?? this.world.enemies.get(id) ?? this.world.npcs.get(id);
  }

  // --- join / lifecycle -------------------------------------------------------

  private handleJoin(token: string) {
    const name = token.trim() || "Traveler";
    this.meId = "player-me";
    const me: ActorState = {
      id: this.meId,
      name,
      position: [9, 8],
      hp: 84,
      max_hp: 100,
      defense: 1,
      attack_damage: 20,
      is_alive: true,
      disposition: "friendly",
    };
    this.world.players.set(this.meId, me);
    const mara = this.world.npcs.get("npc-mara")!;
    mara.party_owner_id = this.meId; // your follower, rebound on join like M8

    this.emit({
      type: "join_ack",
      player_id: this.meId,
      username: name,
      state: snapshot(this.world),
    });
    this.emitState([
      this.event("player_joined", { player_id: this.meId, name, position: me.position }),
      this.event("ambient", { text: "Mara looks up from the fire and waves you over." }),
    ]);
    this.startIdleLife();
  }

  /** Background life: hearth flavor lines and Wren wandering, so the room
   * breathes even when you don't touch anything. */
  private startIdleLife() {
    const ambientTick = () => {
      if (deriveMode(this.world) === "exploration") {
        const line = AMBIENT_LINES[Math.floor(Math.random() * AMBIENT_LINES.length)];
        this.emitState([this.event("ambient", { text: line })]);
      }
      this.timers.push(window.setTimeout(ambientTick, 25000 + Math.random() * 20000));
    };
    this.timers.push(window.setTimeout(ambientTick, 12000));

    const wrenTick = () => {
      const wren = this.world.players.get("player-wren");
      if (wren?.is_alive && deriveMode(this.world) === "exploration") {
        const dirs: [number, number][] = [[1, 0], [-1, 0], [0, 1], [0, -1]];
        const [dx, dy] = dirs[Math.floor(Math.random() * dirs.length)];
        const [nx, ny] = [wren.position[0] + dx, wren.position[1] + dy];
        // Keep Wren loitering near the bar rather than roaming the whole hall.
        if (!this.blocked(nx, ny) && chebyshev([nx, ny], [12, 1]) <= 2) {
          wren.position = [nx, ny];
          this.emitState([]);
        }
      }
      this.timers.push(window.setTimeout(wrenTick, 6000 + Math.random() * 8000));
    };
    this.timers.push(window.setTimeout(wrenTick, 7000));
  }

  // --- actions ----------------------------------------------------------------

  private handleAction(msg: Extract<ClientMessage, { type: "action" }>) {
    const me = this.me();
    if (!me || !me.is_alive) return;
    const events: GameEvent[] = [];
    const modeBefore = deriveMode(this.world);

    if (msg.action_type === "move") {
      const [dx, dy] = msg.direction;
      const [nx, ny] = [me.position[0] + dx, me.position[1] + dy];
      if (this.blocked(nx, ny)) {
        this.emit({ type: "error", message: "The way is blocked." });
        return;
      }
      me.position = [nx, ny];
      this.followMe(events);
    } else if (msg.action_type === "attack") {
      const target = this.findActor(msg.target_id);
      if (!target || !target.is_alive) return;
      if (manhattan(me.position, target.position) !== 1) {
        this.emit({ type: "error", message: `You are too far from ${target.name}.` });
        return;
      }
      this.dealDamage(me, target, me.attack_damage, events);
    } else if (msg.action_type === "bomb") {
      const [tx, ty] = msg.target_tile;
      events.push(this.event("bomb_thrown", { player_id: me.id, tile: [tx, ty], radius: 1 }));
      for (const actor of this.allLiving()) {
        if (Math.abs(actor.position[0] - tx) <= 1 && Math.abs(actor.position[1] - ty) <= 1) {
          this.dealDamage(me, actor, 40, events, "bomb");
        }
      }
    }
    // "wait" falls through: the enemies simply get their turn.

    if (deriveMode(this.world) === "combat") {
      this.enemiesAct(events);
    }
    this.noteModeShift(modeBefore, events);
    this.world.round += 1;
    this.emitState(events);
  }

  /** Mara trails you like a party member should: one step toward you
   * whenever you leave her more than two tiles behind. */
  private followMe(events: GameEvent[]) {
    void events;
    const me = this.me();
    const mara = this.world.npcs.get("npc-mara");
    if (!me || !mara?.is_alive || mara.party_owner_id !== me.id) return;
    if (chebyshev(mara.position, me.position) <= 2) return;
    this.stepToward(mara, me.position);
  }

  private stepToward(actor: ActorState, target: [number, number]) {
    const dx = Math.sign(target[0] - actor.position[0]);
    const dy = Math.sign(target[1] - actor.position[1]);
    const tries: [number, number][] =
      Math.abs(target[0] - actor.position[0]) >= Math.abs(target[1] - actor.position[1])
        ? [[dx, 0], [0, dy], [dx, dy]]
        : [[0, dy], [dx, 0], [dx, dy]];
    for (const [tx, ty] of tries) {
      if (tx === 0 && ty === 0) continue;
      const [nx, ny] = [actor.position[0] + tx, actor.position[1] + ty];
      if (!this.blocked(nx, ny)) {
        actor.position = [nx, ny];
        return;
      }
    }
  }

  private allLiving(): ActorState[] {
    return [
      ...this.world.players.values(),
      ...this.world.enemies.values(),
      ...this.world.npcs.values(),
    ].filter((a) => a.is_alive);
  }

  private dealDamage(
    attacker: ActorState,
    target: ActorState,
    amount: number,
    events: GameEvent[],
    cause: "attack" | "bomb" = "attack",
  ) {
    if (target.id === attacker.id && cause === "attack") return;
    const dealt = Math.max(1, amount - target.defense);
    target.hp = Math.max(0, target.hp - dealt);
    if (cause === "bomb") {
      // Blast damage has no *_attacked intent event, exactly like the server.
      events.push(this.event("entity_damaged", {
        target_id: target.id, damage: dealt, hp_remaining: target.hp,
      }));
    } else {
      const kind = kindOf(attacker);
      events.push(this.event(`${kind}_attacked`, {
        attacker_id: attacker.id,
        ...(kind !== "player" && { attacker_name: attacker.name }),
        target_id: target.id,
        damage: dealt,
      }));
    }
    if (target.hp === 0) {
      target.is_alive = false;
      events.push(this.event(`${kindOf(target)}_died`, { target_id: target.id, killer_id: attacker.id }));
      if (target.id === this.meId) this.scheduleRevival();
    }
  }

  /** Death is a setback, not a terminal loss (GAME_DESIGN.md): you wake by
   * the hearth after a few seconds, patched up and embarrassed. */
  private scheduleRevival() {
    this.timers.push(
      window.setTimeout(() => {
        const me = this.me();
        if (!me || me.is_alive) return;
        me.is_alive = true;
        me.hp = Math.floor(me.max_hp / 2);
        me.position = this.blocked(7, 1) ? [8, 3] : [7, 1];
        this.emitState([
          this.event("revive", {
            text: "Mara drags you to the hearth's warmth. You wake, aching but alive.",
          }),
        ]);
      }, 5000),
    );
  }

  private enemiesAct(events: GameEvent[]) {
    const targets = [...this.world.players.values()].filter((p) => p.is_alive);
    if (targets.length === 0) return;
    const hostiles = [
      ...[...this.world.enemies.values()].filter((e) => e.is_alive),
      ...[...this.world.npcs.values()].filter((n) => n.is_alive && n.disposition === "hostile"),
    ];
    for (const hostile of hostiles) {
      let nearest = targets[0];
      for (const t of targets) {
        if (chebyshev(hostile.position, t.position) < chebyshev(hostile.position, nearest.position))
          nearest = t;
      }
      if (chebyshev(hostile.position, nearest.position) <= 1) {
        this.dealDamage(hostile, nearest, hostile.attack_damage, events);
      } else {
        this.stepToward(hostile, nearest.position);
      }
    }
  }

  private noteModeShift(before: string, events: GameEvent[]) {
    const after = deriveMode(this.world);
    if (before === after) return;
    events.push(this.event("room_mode_changed", { mode: after }));
  }

  // --- dialogue ----------------------------------------------------------------

  private handleTalk(npcId: string, text: string) {
    const me = this.me();
    const npc = this.world.npcs.get(npcId);
    if (!me || !npc || !npc.is_alive) {
      this.emit({ type: "error", message: "There is nobody there to talk to." });
      return;
    }
    if (manhattan(me.position, npc.position) !== 1) {
      this.emit({ type: "error", message: `You are too far from ${npc.name}.` });
      return;
    }

    // A beat of latency so the pending "..." state is visible, like a real LLM call.
    this.timers.push(
      window.setTimeout(() => {
        const modeBefore = deriveMode(this.world);
        const reply = npcReply(npc, text, me);
        const events: GameEvent[] = [];

        for (const effect of reply.effects) {
          if (effect.kind === "heal_player" && effect.amount) {
            me.hp = Math.min(me.max_hp, me.hp + effect.amount);
            events.push(this.event("heal", { name: me.name, amount: effect.amount, by: npc.name }));
          } else if (effect.kind === "set_disposition" && effect.disposition) {
            npc.disposition = effect.disposition;
            events.push(this.event("disposition_changed", {
              target_id: npc.id, disposition: effect.disposition, source_id: me.id,
            }));
          }
        }
        this.noteModeShift(modeBefore, events);
        if (events.length) this.emitState(events);

        this.emit({
          type: "npc_dialogue",
          npc_id: npc.id,
          name: npc.name,
          player_text: text,
          text: reply.text,
        });
      }, 650 + Math.random() * 500),
    );
  }

  // --- objects -----------------------------------------------------------------

  private handleInspect(objectId: string) {
    const obj = this.world.objects.find((o) => o.id === objectId);
    if (!obj) {
      this.emit({ type: "error", message: "Object not found" });
      return;
    }
    this.emit({ type: "object_inspection", object: { ...obj, details: [...obj.details] } });

    // The staged combat demo: first peek at the cellar wakes the rats.
    if (obj.id === "obj-cellar" && !this.world.cellarOpened) {
      this.world.cellarOpened = true;
      this.timers.push(
        window.setTimeout(() => {
          const events: GameEvent[] = [];
          const modeBefore = deriveMode(this.world);
          for (const [x, y] of [[2, 9], [3, 10]] as [number, number][]) {
            if (this.blocked(x, y)) continue;
            const id = `enemy-rat-${++this.ratCount}`;
            this.world.enemies.set(id, {
              id,
              name: "Cellar Rat",
              position: [x, y],
              hp: 30,
              max_hp: 30,
              defense: 0,
              attack_damage: 8,
              is_alive: true,
              disposition: "hostile",
            });
            events.push(this.event("enemy_spawned", { name: "Cellar Rat" }));
          }
          this.noteModeShift(modeBefore, events);
          this.emitState(events);
        }, 900),
      );
    }
  }

  // --- mock-only: items ----------------------------------------------------------

  private handleUseItem(itemId: string, heal: number) {
    const me = this.me();
    if (!me || !me.is_alive) return;
    me.hp = Math.min(me.max_hp, me.hp + heal);
    this.emitState([this.event("item_used", { name: me.name, item_id: itemId, amount: heal })]);
  }
}
