"""How an actor decides to act — the Brain seam (NPCS.md "Followers", ROADMAP M6).

Behavior is NOT a property of an entity (keep the dataclasses generic) and NOT a
kind of thing (NPCS.md "axes, not taxonomy"). It is a strategy chosen from an
actor's DATA — disposition and party membership — every round by `select_brain`.
An "enemy" is just an actor whose disposition is hostile, which is why it gets the
`ChaseBrain`; recruit an NPC and the same function hands it the follower brain;
flip a shopkeeper hostile (M7) and it starts chasing — no class change, just a
field write. This is the "abstract at the second behavior" rule paying out: the
seam arrives now because there is finally a second behavior to justify it.

A brain only DECIDES (returns an `Intent`); the engine DISPOSES — it validates and
resolves the intent through the same rule primitives players obey
(`is_valid_position`, `is_occupied`, adjacency, `compute_damage`). Same
propose/dispose split as dialogue effects, pointed at movement instead of state:
a brain, like the client and the LLM, is never trusted to mutate the world itself.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from backend.config import ENEMY_CHASE_RANGE, FOLLOW_LEASH
from backend.entities import Actor, Disposition, NPC, Player, Position
from backend.room_state import RoomState


@dataclass
class MoveIntent:
    """Step onto `to` (a single orthogonal tile). The engine re-checks the tile
    is free at resolution time — another actor may have taken it this phase."""
    to: Position


@dataclass
class AttackIntent:
    """Strike `target_id`. The engine re-checks adjacency and liveness at
    resolution time (authoritative validation, ARCHITECTURE.md)."""
    target_id: str


# A brain's whole output for one turn: move, attack, or do nothing.
Intent = MoveIntent | AttackIntent | None


class Brain(ABC):
    """One actor's decision policy for one turn. Pure: reads room state, returns
    an intent, mutates nothing. All world change happens when the engine resolves
    the intent — so a brain can never bypass the rules, exactly like a player."""

    @abstractmethod
    def decide(self, room: RoomState, actor: Actor) -> Intent:
        ...


class ChaseBrain(Brain):
    """The hostile default (today's hardcoded enemy AI, now named): attack an
    adjacent target, else step toward the nearest one within chase range. A
    'target' is anyone on the player's side — players AND their followers, so a
    recruited ally is a real combatant enemies will actually fight."""

    def decide(self, room: RoomState, actor: Actor) -> Intent:
        target = _nearest(actor, _player_side(room))
        if target is None:
            return None
        if _manhattan(actor.position, target.position) == 1:
            return AttackIntent(target.id)
        if _manhattan(actor.position, target.position) <= ENEMY_CHASE_RANGE:
            step = _step_toward(room, actor, target)
            if step is not None:
                return MoveIntent(step)
        return None


class FollowerBrain(Brain):
    """Defend-my-owner: strike an adjacent hostile, else close on the nearest
    hostile, else regroup on the owner so you aren't left behind. Followers only
    ever run inside the actor phase, which only combat rooms drive — so in an
    exploration room a follower simply stands (there is nothing to fight)."""

    def decide(self, room: RoomState, actor: Actor) -> Intent:
        enemy = _nearest(actor, _hostiles(room))
        if enemy is not None:
            if _manhattan(actor.position, enemy.position) == 1:
                return AttackIntent(enemy.id)
            step = _step_toward(room, actor, enemy)
            return MoveIntent(step) if step is not None else None
        # No hostiles left: close the gap to the owner only once you've drawn
        # farther than the leash. Hovering a step back (not glued to the owner's
        # tile) is what stops an idle follower from standing in your path.
        owner = room.get_player(actor.party_owner_id) if actor.party_owner_id else None
        if owner is not None and _manhattan(actor.position, owner.position) > FOLLOW_LEASH:
            step = _step_toward(room, actor, owner)
            if step is not None:
                return MoveIntent(step)
        return None


# Brains are stateless — one shared instance each, like the mode singletons.
_CHASE = ChaseBrain()
_FOLLOW = FollowerBrain()


def select_brain(actor: Actor) -> Brain | None:
    """Pick an actor's brain FROM ITS DATA, fresh each turn (this is the whole
    'axes, not taxonomy' payoff). Players are driven by their sockets, not a
    brain. A hostile actor chases — that covers seeded enemies AND any NPC
    soured to hostile. An NPC that has joined a party follows. Everyone else
    stands still. Hostile is checked first, so the follower invariant (an ally
    is always friendly) means these two branches never contend."""
    if not actor.is_alive or isinstance(actor, Player):
        return None
    if actor.disposition is Disposition.HOSTILE:
        return _CHASE
    if isinstance(actor, NPC) and actor.party_owner_id is not None:
        return _FOLLOW
    return None


# --- shared movement/targeting primitives -------------------------------------


def _player_side(room: RoomState) -> list[Actor]:
    """Everyone a hostile actor treats as a target: players and their recruited
    followers. A neutral/friendly NPC that has NOT joined a party is a bystander
    enemies ignore — so a would-be recruit is safe until it actually joins."""
    followers = [n for n in room.living_npcs() if n.party_owner_id is not None]
    return [*room.living_players(), *followers]


def _hostiles(room: RoomState) -> list[Actor]:
    """Every living actor hostile to players — what a follower fights. Derived
    from the disposition field, so a soured NPC is a valid target too."""
    return [a for a in room.living_actors() if a.disposition is Disposition.HOSTILE]


def _manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def _nearest(actor: Actor, candidates: list) -> Actor | None:
    best = None
    best_dist = float("inf")
    for c in candidates:
        dist = _manhattan(actor.position, c.position)
        if dist < best_dist:
            best_dist = dist
            best = c
    return best


def _step_toward(room: RoomState, actor: Actor, target: Actor) -> Position | None:
    """One greedy orthogonal step toward `target`, preferring the longer axis and
    falling back to the other when the preferred tile is blocked. Returns None
    when neither step is currently legal (walls or occupied)."""
    dx = target.position.x - actor.position.x
    dy = target.position.y - actor.position.y

    candidates = []
    if abs(dx) >= abs(dy):
        candidates.append((1 if dx > 0 else -1, 0))
        if dy != 0:
            candidates.append((0, 1 if dy > 0 else -1))
    else:
        candidates.append((0, 1 if dy > 0 else -1))
        if dx != 0:
            candidates.append((1 if dx > 0 else -1, 0))

    for sx, sy in candidates:
        nx, ny = actor.position.x + sx, actor.position.y + sy
        if room.is_valid_position(nx, ny) and not room.is_occupied(nx, ny):
            return Position(nx, ny)
    return None
