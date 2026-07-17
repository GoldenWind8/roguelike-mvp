/**
 * The demo world: one cozy room, "The Hearthstone Hall", staged to show off
 * every UI surface — a party member, a neutral innkeeper who can be talked
 * into (and out of) a fight, a cellar that spawns combat, lore objects, and
 * another "player" who wanders to make the world feel inhabited.
 *
 * This module is pure data + snapshot logic; behavior lives in mockSocket.
 */
import type {
  ActorState,
  Disposition,
  GameEvent,
  NpcState,
  ObjectDetail,
  RoomMode,
  RoomStatePayload,
} from "../net/types";

export interface MockWorld {
  room: { id: string; name: string; width: number; height: number };
  walls: Set<string>; // "x,y"
  objects: ObjectDetail[]; // full detail held server-side; summaries go on the wire
  players: Map<string, ActorState>;
  enemies: Map<string, ActorState>;
  npcs: Map<string, NpcState>;
  round: number;
  cellarOpened: boolean;
}

export const key = (x: number, y: number) => `${x},${y}`;

export function buildWorld(): MockWorld {
  const walls = new Set<string>();
  // The bar counter Gorrik stands behind, with a gap to talk through.
  for (const [x, y] of [[6, 2], [7, 2], [9, 2], [10, 2]]) walls.add(key(x, y));
  // Pillars holding the old roof up — also handy landmarks while the camera pans.
  for (const [x, y] of [[4, 5], [13, 5], [4, 8], [13, 8]]) walls.add(key(x, y));

  const objects: ObjectDetail[] = [
    {
      id: "obj-hearth",
      type: "hearth",
      position: [8, 0],
      label: "Great Hearth",
      description:
        "A stone hearth old enough to have opinions. The fire inside burns low and steady, and the warmth reaches every corner of the hall.",
      details: ["The heat soaks into your bones.", "You feel, briefly, that nothing outside matters."],
    },
    {
      id: "obj-board",
      type: "notice_board",
      position: [0, 4],
      label: "Notice Board",
      description: "A cork board crowded with pinned scraps, some older than the ink on them.",
      details: [
        "“LOST: one cellar key. Reward: stew.” — G.",
        "“The pass to Emberhollow closes with the first snow.”",
        "“Do NOT feed the rats. They remember.”",
      ],
    },
    {
      id: "obj-crate",
      type: "crate",
      position: [15, 10],
      label: "Supply Crate",
      description: "A crate stamped with a merchant's mark, smelling of straw and oranges.",
      details: ["Nailed shut. Gorrik would notice."],
    },
    {
      id: "obj-cellar",
      type: "cellar_door",
      position: [2, 10],
      label: "Cellar Door",
      description: "A trapdoor set into the floorboards. The latch hangs broken.",
      details: ["Something is scratching underneath."],
    },
    {
      id: "obj-door",
      type: "door",
      position: [17, 6],
      label: "Oaken Door",
      description: "The way out into the night. The bolt is drawn and the wind howls beyond.",
      details: ["Room traversal arrives when the real backend is wired in."],
    },
  ];

  const players = new Map<string, ActorState>();
  players.set("player-wren", {
    id: "player-wren",
    name: "Wren",
    position: [12, 1],
    hp: 92,
    max_hp: 100,
    defense: 1,
    attack_damage: 15,
    is_alive: true,
    disposition: "friendly",
  });

  const npcs = new Map<string, NpcState>();
  npcs.set("npc-mara", {
    id: "npc-mara",
    name: "Mara",
    position: [6, 7],
    hp: 60,
    max_hp: 60,
    defense: 1,
    attack_damage: 0,
    is_alive: true,
    disposition: "friendly",
    role: "Wandering Healer",
    party_owner_id: null, // bound to you at join
  });
  npcs.set("npc-gorrik", {
    id: "npc-gorrik",
    name: "Gorrik",
    position: [8, 1],
    hp: 80,
    max_hp: 80,
    defense: 2,
    attack_damage: 10,
    is_alive: true,
    disposition: "neutral",
    role: "Innkeeper",
    party_owner_id: null,
  });

  return {
    // Wider than the viewport on purpose: generated rooms will come in many
    // sizes, so the camera (auto-centred on you, free to scroll) is part of
    // what this mockup has to prove.
    room: { id: "hearthstone-hall", name: "The Hearthstone Hall", width: 18, height: 12 },
    walls,
    objects,
    players,
    enemies: new Map(),
    npcs,
    round: 0,
    cellarOpened: false,
  };
}

/** Live derived mode, exactly like the real engine (M7): the room is in
 * combat while any living hostile is present. */
export function deriveMode(world: MockWorld): RoomMode {
  for (const e of world.enemies.values()) if (e.is_alive && e.disposition === "hostile") return "combat";
  for (const n of world.npcs.values()) if (n.is_alive && n.disposition === "hostile") return "combat";
  return "exploration";
}

export function snapshot(world: MockWorld): RoomStatePayload {
  const grid: (string | null)[][] = Array.from({ length: world.room.height }, () =>
    Array.from({ length: world.room.width }, () => null),
  );
  const place = (a: ActorState) => {
    if (a.is_alive) grid[a.position[1]][a.position[0]] = a.id;
  };
  world.players.forEach(place);
  world.enemies.forEach(place);
  world.npcs.forEach(place);

  return {
    room: { ...world.room, mode: deriveMode(world) },
    round: world.round,
    grid,
    walls: [...world.walls].map((k) => k.split(",").map(Number) as [number, number]),
    objects: world.objects.map(({ id, type, position, label }) => ({ id, type, position, label })),
    players: Object.fromEntries([...world.players.entries()].map(([id, p]) => [id, { ...p }])),
    enemies: Object.fromEntries([...world.enemies.entries()].map(([id, e]) => [id, { ...e }])),
    npcs: Object.fromEntries([...world.npcs.entries()].map(([id, n]) => [id, { ...n }])),
    pending_player_ids: [],
    started: true,
    phase: deriveMode(world) === "combat" ? "action" : "exploring",
  };
}

export function makeEvent(type: string, data: Record<string, unknown>, round: number): GameEvent {
  return { event_type: type, data, round };
}

// --- scripted dialogue --------------------------------------------------------
// Stands in for the real LLM + validated effect channel: the reply carries
// prose plus zero or more effects the "server" applies before answering,
// which is exactly how the live dialogue pipeline behaves.

export interface DialogueEffect {
  kind: "heal_player" | "set_disposition";
  amount?: number;
  disposition?: Disposition;
}

export interface DialogueReply {
  text: string;
  effects: DialogueEffect[];
}

const has = (text: string, words: string[]) => {
  const lower = text.toLowerCase();
  return words.some((w) => lower.includes(w));
};

let maraIdle = 0;
const MARA_IDLE = [
  "The fire's kind tonight. Sit a while before you go wandering.",
  "I'll follow where you lead. Just — let's not lead anywhere damp.",
  "Every scar has a story. Most of mine are about doors I shouldn't have opened.",
];

let gorrikIdle = 0;
const GORRIK_IDLE = [
  "Wipe your boots. The ale's warm, the stew's warmer.",
  "Strange lights over the pass lately. Travelers talk.",
  "That healer you walk with — she paid for both your beds. Good sort.",
];

export function npcReply(npc: NpcState, text: string, me: ActorState): DialogueReply {
  if (npc.id === "npc-mara") {
    if (has(text, ["heal", "hurt", "wound", "help me"])) {
      if (me.hp >= me.max_hp) {
        return { text: "You're whole already. Save my herbs for real wounds.", effects: [] };
      }
      return {
        text: "Hold still — this will sting less than whatever bit you.",
        effects: [{ kind: "heal_player", amount: 30 }],
      };
    }
    if (has(text, ["rat", "cellar", "scratch"])) {
      return {
        text: "I've heard the scratching under the floorboards too. Gorrik pretends not to.",
        effects: [],
      };
    }
    return { text: MARA_IDLE[maraIdle++ % MARA_IDLE.length], effects: [] };
  }

  // Gorrik: the escalation demo. Rude words sour him; an apology calms him.
  if (npc.disposition === "hostile") {
    if (has(text, ["sorry", "apolog", "peace", "calm", "mistake"])) {
      return {
        text: "…Hmph. The hearth makes fools of us all. Sit down before I change my mind.",
        effects: [{ kind: "set_disposition", disposition: "neutral" }],
      };
    }
    return { text: "Gorrik snarls and hefts the iron poker. Wrong answer.", effects: [] };
  }
  if (has(text, ["stupid", "idiot", "useless", "coward", "fool", "fight", "watered"])) {
    return {
      text: "Gorrik slams a tankard down hard enough to crack it. “Say that again, whelp.”",
      effects: [{ kind: "set_disposition", disposition: "hostile" }],
    };
  }
  if (has(text, ["rat", "cellar"])) {
    return {
      text: "Rats? In MY cellar? …Keep your voice down. If you must go poking that trapdoor, take a bomb.",
      effects: [],
    };
  }
  if (has(text, ["room", "bed", "sleep", "stay"])) {
    return { text: "Rooms are two coppers. The hearth is free, and better company.", effects: [] };
  }
  return { text: GORRIK_IDLE[gorrikIdle++ % GORRIK_IDLE.length], effects: [] };
}

export const AMBIENT_LINES = [
  "The fire pops and settles.",
  "Wind rattles the shutters, politely, then gives up.",
  "Somewhere upstairs, a floorboard sighs.",
  "The kettle over the hearth begins to murmur.",
  "Rain taps at the windows like it wants in.",
];
