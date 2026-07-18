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
  ItemView,
  ServerMessage,
} from "./types";
import {
  AMBIENT_LINES,
  buildWorld,
  deriveMode,
  key,
  makeEvent,
  mockLootRoll,
  npcReply,
  snapshot,
  starterPack,
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
      case "open_chest":
        this.handleOpenChest(msg.object_id);
        break;
      case "take_item":
        this.handleTakeItem(msg.object_id, msg.index, msg.item_id);
        break;
      case "equip":
      case "unequip":
        this.handleEquip(msg.slot, msg.type === "equip");
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
      inventory: starterPack(),
      active_effects: [],
      hunger: 64,
      max_hunger: 100,
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

    // The hunger clock, mock edition: a slow drain so the bar visibly moves,
    // starvation chip damage at zero — same shape as the real world ticker.
    const hungerTick = () => {
      const me = this.me();
      if (me?.is_alive && me.hunger !== undefined) {
        const wasStarving = me.hunger <= 0;
        me.hunger = Math.max(0, me.hunger - 1);
        const events: GameEvent[] = [];
        if (me.hunger <= 0) {
          if (!wasStarving) events.push(this.event("player_starving", { target_id: me.id }));
          me.hp = Math.max(0, me.hp - 1);
          events.push(this.event("entity_damaged", { target_id: me.id, damage: 1, hp_remaining: me.hp }));
          if (me.hp === 0) {
            me.is_alive = false;
            events.push(this.event("player_died", { target_id: me.id, killer_id: null }));
            this.scheduleRevival();
          }
        } else if (me.hunger >= 80 && me.hp < me.max_hp) {
          me.hp = Math.min(me.max_hp, me.hp + 1);
          events.push(this.event("entity_healed", { target_id: me.id, amount: 1, hp: me.hp }));
        }
        this.emitState(events);
      }
      this.timers.push(window.setTimeout(hungerTick, 9000));
    };
    this.timers.push(window.setTimeout(hungerTick, 9000));

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
    } else if (msg.action_type === "consume") {
      const slot = me.inventory?.[msg.slot];
      if (!slot || slot.item.type !== "consumable") {
        this.emit({ type: "error", message: "No such inventory slot" });
        return;
      }
      this.spendSlot(me, msg.slot);
      events.push(this.event("item_consumed", { player_id: me.id, item: slot.item }));
      for (const atom of slot.item.payload.effects ?? []) {
        if (atom.kind === "restore_hp") {
          const healed = Math.min(atom.amount, me.max_hp - me.hp);
          me.hp += healed;
          events.push(this.event("entity_healed", { target_id: me.id, amount: healed, hp: me.hp }));
        } else if (atom.kind === "restore_hunger") {
          const max = me.max_hunger ?? 100;
          const fed = Math.min(atom.amount, max - (me.hunger ?? max));
          me.hunger = (me.hunger ?? max) + fed;
          events.push(this.event("hunger_restored", { target_id: me.id, amount: fed, hunger: me.hunger }));
        } else if (atom.kind === "stat_mod" && atom.stat) {
          (me.active_effects ??= []).push({
            stat: atom.stat, amount: atom.amount,
            source: slot.item.name, remaining_s: atom.duration_s ?? 60,
          });
          events.push(this.event("effect_applied", {
            target_id: me.id, stat: atom.stat, amount: atom.amount,
            duration_s: atom.duration_s, source: slot.item.name,
          }));
        }
      }
    } else if (msg.action_type === "throw") {
      const slot = me.inventory?.[msg.slot];
      if (!slot || slot.item.type !== "throwable") {
        this.emit({ type: "error", message: "No such inventory slot" });
        return;
      }
      const [tx, ty] = msg.target_tile;
      const radius = slot.item.payload.area?.size ?? 0;
      this.spendSlot(me, msg.slot);
      events.push(this.event("item_thrown", { player_id: me.id, item: slot.item, tile: [tx, ty], radius }));
      const damage = slot.item.payload.effects?.find((a) => a.kind === "damage")?.amount ?? 0;
      for (const actor of this.allLiving()) {
        if (manhattan(actor.position, [tx, ty]) <= radius && damage > 0) {
          this.dealDamage(me, actor, damage, events, "bomb");
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

  // --- loot (docs/LOOT.md, mock edition) -----------------------------------------

  /** One copy out of a pack slot; the slot vanishes at zero. */
  private spendSlot(me: ActorState, index: number) {
    const pack = me.inventory ?? [];
    const slot = pack[index];
    if (!slot) return;
    slot.quantity -= 1;
    if (slot.quantity <= 0) pack.splice(index, 1);
  }

  private addToPack(me: ActorState, item: ItemView): boolean {
    const pack = (me.inventory ??= []);
    const stackable = item.type === "consumable" || item.type === "throwable";
    if (stackable) {
      const existing = pack.find((s) => s.item.id === item.id);
      if (existing) {
        existing.quantity += 1;
        return true;
      }
    }
    if (pack.length >= 10) return false;
    pack.push({ item, quantity: 1, equipped: false });
    return true;
  }

  private handleOpenChest(objectId: string) {
    const me = this.me();
    const chest = this.world.objects.find((o) => o.id === objectId);
    if (!me || !me.is_alive || !chest || chest.type !== "chest") {
      this.emit({ type: "error", message: "There is no chest there." });
      return;
    }
    if (manhattan(me.position, chest.position) > 1) {
      this.emit({ type: "error", message: "You are too far from the chest." });
      return;
    }

    if (chest.opened) {
      // Peeking: show what still waits — the popup handles the taking.
      if (!chest.contents?.length) {
        this.emit({ type: "error", message: "The chest is empty." });
        return;
      }
      this.emit({
        type: "chest_contents",
        object_id: chest.id,
        items: chest.contents.map((item) => ({ item, minted: false })),
      });
      return;
    }

    // First-to-open: roll 1-3 finds INTO the chest, with a beat of suspense
    // like the real LLM path (weights mirror CHEST_ITEM_COUNT_WEIGHTS).
    chest.opened = true;
    this.timers.push(
      window.setTimeout(() => {
        const roll = Math.random();
        const count = roll < 0.6 ? 1 : roll < 0.9 ? 2 : 3;
        const events: GameEvent[] = [];
        const finds = Array.from({ length: count }, () => {
          const item = mockLootRoll();
          const minted = Math.random() < 0.1;
          if (minted) events.push(this.event("item_generated", { item }));
          (chest.contents ??= []).push(item);
          return { item, minted };
        });
        events.push(this.event("chest_opened", {
          player_id: me.id, object_id: chest.id, items: finds,
        }));
        this.emitState(events);
      }, 400 + Math.random() * 600),
    );
  }

  private handleTakeItem(objectId: string, index: number, itemId: number) {
    const me = this.me();
    const chest = this.world.objects.find((o) => o.id === objectId);
    if (!me || !me.is_alive || !chest || chest.type !== "chest" || !chest.opened) {
      this.emit({ type: "error", message: "There is no chest there." });
      return;
    }
    if (manhattan(me.position, chest.position) > 1) {
      this.emit({ type: "error", message: "You are too far from the chest." });
      return;
    }
    const contents = chest.contents ?? [];
    if (!(index >= 0 && index < contents.length) || contents[index].id !== itemId) {
      this.emit({ type: "error", message: "That's already been taken." });
      return;
    }
    if (!this.addToPack(me, contents[index])) {
      this.emit({ type: "error", message: "Your pack is full." });
      return;
    }
    const [item] = contents.splice(index, 1);
    this.emitState([this.event("chest_looted", { player_id: me.id, object_id: chest.id, item })]);
  }

  private handleEquip(index: number, equipping: boolean) {
    const me = this.me();
    const pack = me?.inventory ?? [];
    const slot = pack[index];
    if (!me || !slot) {
      this.emit({ type: "error", message: "No such inventory slot" });
      return;
    }
    if (slot.item.type !== "weapon" && slot.item.type !== "wearable") {
      this.emit({ type: "error", message: `You can't equip a ${slot.item.type}` });
      return;
    }
    if (equipping) {
      if (slot.item.type === "weapon") {
        for (const other of pack) {
          if (other.equipped && other.item.type === "weapon") other.equipped = false;
        }
      }
      slot.equipped = true;
    } else {
      slot.equipped = false;
    }
    this.emitState([this.event(equipping ? "item_equipped" : "item_unequipped", {
      player_id: me.id, slot: index, item: slot.item,
    })]);
  }
}
