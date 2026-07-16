from backend.actions import Action, ActionType
from backend.config import RNG_SEED
from backend.dialogue_effects import process_proposals
from backend.entities import NPC, Player, Position
from backend.events import GameEvent, EventType
from backend.room_loader import RoomTemplate
from backend.modes import MODES, derive_mode
from backend.systems import resolve_round
from backend.room_state import RoomState


class RoomEngine:
    def __init__(self, template: RoomTemplate, seed: int = RNG_SEED):
        self.room = RoomState(template, seed)
        # Mode is DERIVED, never configured (M7): the template's seeded enemies
        # are already placed by RoomState above, so the predicate sees them.
        # Individuals load after construction — the loader calls refresh_mode()
        # once they're placed, so a room soured on a past visit wakes up combat.
        self.mode_name = derive_mode(self.room)
        self.mode = MODES[self.mode_name]
        self.started = False
        self.phase = "waiting"

    @property
    def turn_based(self) -> bool:
        """Does this room buffer actions into rounds? (Round timers and
        waiting_for broadcasts only make sense when this is True.)"""
        return self.mode.turn_based

    def join(self, player_name: str) -> tuple[Player, list[GameEvent]]:
        # Room capacity = number of spawn points (replaces the old MAX_PLAYERS).
        if len(self.room.players) >= self.room.template.capacity:
            raise ValueError("Room is full")

        player = self.room.add_player(player_name)

        events = [GameEvent(
            EventType.PLAYER_JOINED,
            {"player_id": player.id, "name": player.name, "position": [player.position.x, player.position.y]},
            self.room.round,
        )]

        if len(self.room.players) >= 1 and not self.started:
            self.started = True
            self.phase = "player_phase"
            events.append(GameEvent(
                EventType.ROUND_STARTED,
                {"round": self.room.round},
                self.room.round,
            ))

        return player, events

    def attach_player(self, player: Player) -> list[GameEvent]:
        """Traversal arrival: place an EXISTING player (hp/id/name preserved)
        at a free spawn. Raises ValueError when the room can't take them —
        the caller denies the traversal and the player stays where they were."""
        if len(self.room.players) >= self.room.template.capacity:
            raise ValueError("The way is blocked")
        spawn = self.room.free_spawn()
        if spawn is None:
            raise ValueError("The way is blocked")

        self.room.attach_player(player, Position(spawn[0], spawn[1]))

        events = [GameEvent(
            EventType.PLAYER_JOINED,
            {"player_id": player.id, "name": player.name, "position": [player.position.x, player.position.y]},
            self.room.round,
        )]

        # Same start logic as join(): arriving in a dormant room wakes it.
        if not self.started:
            self.started = True
            self.phase = "player_phase"
            events.append(GameEvent(
                EventType.ROUND_STARTED,
                {"round": self.room.round},
                self.room.round,
            ))

        return events

    def detach_player(self, player_id: str) -> Player | None:
        """Traversal departure. No PLAYER_LEFT event (the resolution's
        PLAYER_ENTERED_DOOR already tells the story) and no auto-resolve —
        traversal happens after a round resolves, so nobody is pending on us."""
        player = self.room.detach_player(player_id)
        if player is None:
            return None
        if not self.room.living_players():
            self.started = False
            self.phase = "waiting"
        return player

    def submit_action(self, player_id: str, action_data: dict) -> tuple[list[GameEvent], bool]:
        """Returns (events, resolved). If resolved is True, broadcast state to
        all. The room's mode owns the timing: combat buffers into rounds,
        exploration applies valid actions immediately (backend/modes.py)."""

        if self.phase != "player_phase":
            return ([GameEvent(
                EventType.INVALID_ACTION,
                {"reason": "Not accepting actions right now"},
                self.room.round,
            )], False)

        return self.mode.submit(self, player_id, action_data)

    def apply_dialogue_effects(
        self, npc: NPC, proposals: list[dict], player: Player | None
    ) -> list[GameEvent]:
        """The out-of-band mutation entry point: validate and apply a dialogue
        reply's effect proposals, THEN re-derive the room mode — one call, so
        no transport layer can apply effects and forget the refresh (the
        invariant is 'mode is re-checked after every batch of world mutations',
        and this is the only batch that happens outside a round). A future
        out-of-band effect source (items, scripted triggers) should come
        through here — or through a sibling that generalizes this — rather
        than calling process_proposals directly."""
        events = process_proposals(self.room, npc, proposals, player=player)
        events.extend(self.refresh_mode())
        return events

    def refresh_mode(self) -> list[GameEvent]:
        """Re-evaluate the M7 predicate and swap timing models if its answer
        changed. Called at every resolution point: after a round resolves
        (below) and after dialogue effects land (main.handle_talk) — the two
        places a hostile can appear or the last one can fall.

        Escalating announces the mode and starts the first combat round;
        de-escalating drops any half-collected round (those intents belonged
        to a fight that no longer exists) and movement is immediate again.
        The round TIMER lives in main.py, which cancels it on de-escalation."""
        new_name = derive_mode(self.room)
        if new_name == self.mode_name:
            return []
        self.mode_name = new_name
        self.mode = MODES[new_name]

        events = [GameEvent(
            EventType.ROOM_MODE_CHANGED,
            {"mode": new_name},
            self.room.round,
        )]
        if self.mode.turn_based:
            events.append(GameEvent(
                EventType.ROUND_STARTED,
                {"round": self.room.round},
                self.room.round,
            ))
        else:
            self.room.pending_actions.clear()
        return events

    def force_resolve(self) -> list[GameEvent]:
        """Called by timeout — auto-wait for anyone who hasn't acted."""
        for pid in self.room.players_pending():
            self.room.pending_actions[pid] = Action(
                action_type=ActionType.WAIT,
                player_id=pid,
            )
        events, _ = self._resolve_current_round()
        return events

    def _resolve_current_round(self) -> tuple[list[GameEvent], bool]:
        self.phase = "resolving"
        actions = dict(self.room.pending_actions)
        self.room.pending_actions.clear()
        events = resolve_round(self.room, actions)
        # A round can change the predicate's answer (the last hostile died) —
        # re-derive so a cleared room is explorable without a reload.
        events.extend(self.refresh_mode())
        self.phase = "player_phase"
        return (events, True)

    def get_state(self) -> dict:
        state = self.room.to_dict()
        # The LIVE derived mode (M7) — the engine owns it, so RoomState no
        # longer reports the template's static default.
        state["room"]["mode"] = self.mode_name
        state["started"] = self.started
        state["phase"] = self.phase
        return state

    def remove_player(self, player_id: str) -> tuple[list[GameEvent], bool]:
        player = self.room.get_player(player_id)
        if not player:
            return ([], False)

        events = [GameEvent(
            EventType.PLAYER_LEFT,
            {"player_id": player_id, "name": player.name},
            self.room.round,
        )]

        self.room.remove_player(player_id)

        living = self.room.living_players()
        if self.started and len(living) == 0:
            self.started = False
            self.phase = "waiting"
        elif self.started and not self.room.players_pending():
            resolve_events, _ = self._resolve_current_round()
            events.extend(resolve_events)
            return (events, True)

        return (events, False)
