import itertools
import random

from backend.actions import Action
from backend.entities import Actor, Enemy, NPC, Player, Position
from backend.config import PLAYER_MAX_HP, PLAYER_DEFENSE
from backend.events import GameEvent, EventType
from backend.room_loader import RoomTemplate


# Process-unique player ids. Per-room counters would collide once players can
# leave a room, have it evicted+reloaded (fresh counter), and walk back in.
# Enemy ids stay per-room — enemies never cross rooms.
_player_ids = itertools.count(1)


class RoomState:
    def __init__(self, template: RoomTemplate, seed: int):
        self.template = template
        self.rng = random.Random(seed)  #TODO consider removing seed
        self.round = 0
        self.players: dict[str, Player] = {}
        self.enemies: dict[str, Enemy] = {}
        # Individuals (NPCS.md Decision 9): loaded from instance rows, never
        # from the template — the two occupant sources of a room.
        self.npcs: dict[str, NPC] = {}
        self.walls: set[tuple[int, int]] = set(template.walls)
        self.objects = list(template.objects)
        self.pending_actions: dict[str, Action] = {}
        self._next_enemy_num = 1

        self.grid: list[list[str | None]] = [
            [None for _ in range(template.width)]
            for _ in range(template.height)
        ]

        for enemy_def in template.enemies:
            pos = enemy_def.position
            self.add_enemy(
                name=enemy_def.name,
                position=Position(pos[0], pos[1]),
                hp=enemy_def.hp,
                attack_damage=enemy_def.attack_damage,
                defense=enemy_def.defense,
            )

    def add_player(self, name: str) -> Player:
        # Keep the "player_" prefix — get_entity dispatches on it.
        player_id = f"player_{next(_player_ids)}"

        spawn = self.template.spawn_points[len(self.players)]
        position = Position(spawn[0], spawn[1])

        player = Player(
            id=player_id,
            name=name,
            position=position,
            hp=PLAYER_MAX_HP,
            max_hp=PLAYER_MAX_HP,
            defense=PLAYER_DEFENSE,
        )

        self.players[player_id] = player
        self.grid[position.y][position.x] = player_id
        return player

    def add_enemy(self, name: str, position: Position, hp: int, attack_damage: int, defense: int) -> Enemy:
        enemy_id = f"enemy_{self._next_enemy_num}"
        self._next_enemy_num += 1

        enemy = Enemy(
            id=enemy_id,
            name=name,
            position=position,
            hp=hp,
            max_hp=hp,
            attack_damage=attack_damage,
            defense=defense,
        )

        self.enemies[enemy_id] = enemy
        self.grid[position.y][position.x] = enemy_id
        return enemy

    def add_npc(self, npc: NPC) -> NPC:
        """Place a loaded individual. NPCs occupy their tile (NPCS.md
        Decision 2) — dead ones don't, matching how _kill_entity clears the
        grid for any actor."""
        self.npcs[npc.id] = npc
        if npc.is_alive:
            self.grid[npc.position.y][npc.position.x] = npc.id
        return npc

    def remove_player(self, player_id: str):
        player = self.players.get(player_id)
        if not player:
            return
        self.grid[player.position.y][player.position.x] = None
        self.pending_actions.pop(player_id, None)
        del self.players[player_id]

    def detach_player(self, player_id: str) -> Player | None:
        """Remove a player from this room but keep the live Player object —
        traversal moves the same entity (id, hp, name) into another room.
        Unlike remove_player's callers, this emits no PLAYER_LEFT semantics."""
        player = self.players.get(player_id)
        if not player:
            return None
        self.grid[player.position.y][player.position.x] = None
        self.pending_actions.pop(player_id, None)
        del self.players[player_id]
        return player

    def attach_player(self, player: Player, position: Position):
        """Place an existing Player (arriving via traversal) into this room."""
        player.position = position
        self.players[player.id] = player
        self.grid[position.y][position.x] = player.id

    def free_spawn(self) -> tuple[int, int] | None:
        """First unoccupied spawn point, or None if all are blocked (players
        and enemies both occupy grid cells, so a parked enemy blocks a spawn)."""
        for x, y in self.template.spawn_points:
            if not self.is_occupied(x, y):
                return (x, y)
        return None

    def get_player(self, player_id: str) -> Player | None:
        return self.players.get(player_id)

    def get_entity(self, entity_id: str) -> Actor | None:
        if entity_id.startswith("player_"):
            return self.players.get(entity_id)
        elif entity_id.startswith("enemy_"):
            return self.enemies.get(entity_id)
        elif entity_id.startswith("npc_"):
            return self.npcs.get(entity_id)
        return None

    def get_object(self, object_id: str):
        for obj in self.objects:
            if obj.id == object_id:
                return obj
        return None

    def is_valid_position(self, x: int, y: int) -> bool:
        if x < 0 or x >= self.template.width or y < 0 or y >= self.template.height:
            return False
        return (x, y) not in self.walls

    def is_occupied(self, x: int, y: int) -> str | None:
        return self.grid[y][x]

    def move_entity(self, entity_id: str, new_pos: Position):
        entity = self.get_entity(entity_id)
        if not entity:
            return
        self.grid[entity.position.y][entity.position.x] = None
        entity.position = new_pos
        self.grid[new_pos.y][new_pos.x] = entity_id

    def swap_positions(self, a_id: str, b_id: str):
        """Exchange two occupants' tiles in one move — used when a player steps
        into their own follower (handlers._is_own_follower). Both tiles are
        overwritten, so neither is left dangling; no is_occupied check because
        the callers already know the two cells hold exactly a and b."""
        a = self.get_entity(a_id)
        b = self.get_entity(b_id)
        if not a or not b:
            return
        a.position, b.position = b.position, a.position
        self.grid[a.position.y][a.position.x] = a_id
        self.grid[b.position.y][b.position.x] = b_id

    def living_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_alive]

    def living_enemies(self) -> list[Enemy]:
        return [e for e in self.enemies.values() if e.is_alive]

    def living_npcs(self) -> list[NPC]:
        return [n for n in self.npcs.values() if n.is_alive]

    def living_actors(self) -> list[Actor]:
        """Everything that can take damage — area effects target this, so
        they never silently skip a category of actor."""
        return [*self.living_players(), *self.living_enemies(), *self.living_npcs()]

    def players_pending(self) -> list[str]:
        living_ids = {p.id for p in self.living_players()}
        submitted_ids = set(self.pending_actions.keys())
        return sorted(living_ids - submitted_ids)

    def to_dict(self) -> dict:
        return {
            "room": {
                "id": self.template.room_id,
                "name": self.template.room_name,
                "width": self.template.width,
                "height": self.template.height,
                "mode": self.template.mode,
            },
            "round": self.round,
            "grid": self.grid,
            "walls": list(self.walls),
            "objects": [obj.to_summary_dict() for obj in self.objects],
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
            "enemies": {eid: e.to_dict() for eid, e in self.enemies.items()},
            "npcs": {nid: n.to_dict() for nid, n in self.npcs.items()},
            "pending_player_ids": self.players_pending(),
        }
