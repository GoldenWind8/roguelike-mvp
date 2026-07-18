import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import auth
from backend.config import (
    DEV_MODE,
    NPC_TRANSCRIPT_LIMIT,
    TALK_TEXT_LIMIT,
    TURN_TIMEOUT,
    WORLD_TICK_INTERVAL,
)
from backend.db import SessionMaker, init_db
from backend.dialogue import build_provider
from backend.entities import NPC, Player, Position
from backend.events import EventType, GameEvent
from backend.hunger import tick_room_hunger
from backend.inventory import add_item, equip, prune_expired, unequip
from backend.loot import roll_item_count, spawn_loot
from backend.models import ObjectType
from backend.npc_store import load_npcs, save_npcs
from backend.player_store import (
    UsernameTaken,
    authenticate,
    get_player_row,
    make_live_player,
    register_player,
    save_players,
)
from backend.room_engine import RoomEngine
from backend.room_loader import load_room
from backend.seeds import get_or_seed_default_room, reset_npcs, seed_items_if_missing


@dataclass
class RoomRuntime:
    """The unit of ownership for one live room: its RoomEngine, the sockets of
    the players inside it, and its round timer. Everything that must change
    together lives behind this boundary — broadcasts are scoped to a room,
    never to "the server". (This is the in-process seam that later scales:
    the registry dict below becomes Redis routing + worker ownership without
    the game rules ever noticing. See docs/WORLD.md / FUTURE_BACKEND.md.)
    """
    room_id: int
    engine: RoomEngine
    connections: dict[str, WebSocket] = field(default_factory=dict)
    timeout_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build db tables, seed-if-empty, and remember where new players start.
    # Rooms themselves load lazily on first entry (get_or_load_room).
    global default_room_id
    await init_db()
    async with SessionMaker() as session:
        room = await get_or_seed_default_room(session)
        default_room_id = room.id
        # The global item pool (docs/LOOT.md): backfilled only when the items
        # table has never held a row — LLM-grown pools are never diluted.
        await seed_items_if_missing(session)
    # The world-clock sweep (docs/LOOT.md Decision 3): expiry is checked
    # lazily at every stat read, so this ticker only bounds how long a
    # lapsed buff can linger on screen before clients hear about it.
    ticker = asyncio.create_task(world_ticker())
    yield
    ticker.cancel()
    # Shutdown: rooms still live (players connected) never went through
    # eviction, so their individuals must be saved here or a restart would
    # be the one remaining way to destroy an NPC's state.
    async with state_lock:
        for runtime in active_rooms.values():
            await _save_individuals(runtime)


app = FastAPI(lifespan=lifespan)

# Registry of live rooms + who is where. One global lock still serializes all
# mutation (per-room locks are a later optimization, not needed at this scale);
# because every registry read/write happens under it, a room can never be
# double-loaded.
active_rooms: dict[int, RoomRuntime] = {}
player_room: dict[str, int] = {}
state_lock = asyncio.Lock()
default_room_id: int | None = None

# Dialogue source — LLM with canned fallback when a key is configured,
# canned-only otherwise. One instance per process (it owns an HTTP client).
dialogue_provider = build_provider()
# Players with an LLM call in flight: one talk at a time per player, so a
# spamming client can't fan out provider calls (the grid caps 30 req/min/IP).
talking_players: set[str] = set()

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend-react" / "dist"

# Vite emits fingerprinted JS/CSS into dist/assets. check_dir=False keeps
# backend-only commands (including tests) importable before the frontend has
# been built; requesting the UI gives a clear error below instead.
app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False),
    name="frontend-assets",
)


@app.get("/")
async def serve_index():
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(
            503,
            "Frontend is not built. Run `npm ci` and `npm run build` in frontend-react.",
        )
    return FileResponse(index)


# --- accounts (ACCOUNTS.md M8) ------------------------------------------------
# Login over HTTP, play over WebSocket (Decision 5): these two endpoints turn
# credentials into a signed token; the WS join handler turns the token back
# into a player row. Passwords exist only inside these handlers and leave only
# as bcrypt hashes — they are never logged and never stored raw.


class Credentials(BaseModel):
    username: str
    password: str
    email: str | None = None  # register only; stored unverified (Decision 4)


def _session_payload(row) -> dict:
    return {
        "token": auth.sign_token(row.id),
        "player_id": row.id,
        "username": row.username,
    }


@app.post("/register", status_code=201)
async def register(creds: Credentials):
    username = creds.username.strip()
    if not (3 <= len(username) <= 20):
        raise HTTPException(400, "Username must be 3-20 characters.")
    # bcrypt reads at most 72 BYTES of input — reject longer instead of
    # silently truncating (a user's 80-char passphrase should not verify on
    # its first 72 bytes).
    if not (6 <= len(creds.password.encode()) <= 72):
        raise HTTPException(400, "Password must be 6-72 characters.")
    async with SessionMaker() as session:
        try:
            row = await register_player(session, username, creds.password, creds.email)
        except UsernameTaken:
            raise HTTPException(409, "That username is taken.")
    return _session_payload(row)


@app.post("/login")
async def login(creds: Credentials):
    async with SessionMaker() as session:
        row = await authenticate(session, creds.username.strip(), creds.password)
    if row is None:
        # One message for unknown user AND wrong password — don't confirm
        # which usernames exist.
        raise HTTPException(401, "Wrong username or password.")
    return _session_payload(row)


# --- room registry -----------------------------------------------------------
# Lock discipline: every helper below assumes the CALLER holds state_lock and
# never acquires it itself (asyncio.Lock is not reentrant — a helper that
# locked would deadlock when called from a locked handler).


async def get_or_load_room(room_id: int) -> RoomRuntime:
    """Caller holds state_lock. Return the live runtime for a room, loading it
    from the DB on first entry. DB touched only here — never in the hot loop."""
    runtime = active_rooms.get(room_id)
    if runtime is None:
        async with SessionMaker() as session:
            template = await load_room(session, room_id)
            npcs = await load_npcs(session, room_id)
        runtime = RoomRuntime(room_id=room_id, engine=RoomEngine(template))
        # Two occupant sources (NPCS.md Decision 9): the template reseeds
        # fungible enemies above; individuals arrive from their own rows.
        for npc in npcs:
            runtime.engine.room.add_npc(npc)
        # Individuals can change the mode predicate's answer — an NPC soured on
        # a past visit reloads hostile, so the room must wake up combat
        # ("escalation persists", ROADMAP M7). Events are dropped: this is the
        # room's initial mode, not a transition anyone is present to witness.
        runtime.engine.refresh_mode()
        active_rooms[room_id] = runtime
    return runtime


async def _save_individuals(runtime: RoomRuntime) -> None:
    """Persist a room's individuals: NPC rows and (M8) player rows. At the
    eviction save site the room is empty of players by definition, so the
    player half only fires from shutdown — disconnect saves its one leaver
    via _save_player."""
    npcs = list(runtime.engine.room.npcs.values())
    players = list(runtime.engine.room.players.values())
    if not npcs and not players:
        return
    try:
        async with SessionMaker() as session:
            if npcs:
                await save_npcs(session, npcs)
            if players:
                await save_players(session, players, runtime.room_id)
    except Exception:
        # A failed save must not take the room registry down with it — the
        # in-memory state is still authoritative until the next save point.
        logging.exception("failed to save individuals for room %s", runtime.room_id)


async def _save_player(player: Player, room_id: int) -> None:
    """Persist one player's state at the disconnect edge (ACCOUNTS.md
    Decision 7). Same failure posture as _save_individuals: log and move on."""
    try:
        async with SessionMaker() as session:
            await save_players(session, [player], room_id)
    except Exception:
        logging.exception("failed to save player %s", player.id)


async def maybe_evict(runtime: RoomRuntime) -> None:
    """Caller holds state_lock. Drop an empty room from the registry.
    Eviction is "save individuals, unload" (NPCS.md Decision 10): NPC rows
    are written back first; fungible enemy state is deliberately forgotten —
    the next visit reseeds enemies from the template (respawn is a feature)
    and reloads individuals from their rows."""
    if runtime.engine.room.players:
        return
    # Stop the round timer BEFORE the (awaiting) save: don't leave a timer armed
    # while we do async I/O during teardown. cancel_round_timeout has no await,
    # so the timer coroutine can't sneak in and force-resolve a room we're
    # dropping.
    cancel_round_timeout(runtime)
    await _save_individuals(runtime)
    if active_rooms.get(runtime.room_id) is runtime:
        del active_rooms[runtime.room_id]


# --- room-scoped messaging ----------------------------------------------------


async def broadcast(runtime: RoomRuntime, message: dict):
    disconnected = []
    for pid, ws in runtime.connections.items():
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(pid)
    for pid in disconnected:
        runtime.connections.pop(pid, None)


async def send_to(runtime: RoomRuntime, player_id: str, message: dict):
    ws = runtime.connections.get(player_id)
    if ws:
        try:
            await ws.send_json(message)
        except Exception:
            runtime.connections.pop(player_id, None)


async def broadcast_state_and_events(runtime: RoomRuntime, events):
    await broadcast(runtime, {
        "type": "state_update",
        "state": runtime.engine.get_state(),
        "events": [e.to_dict() for e in events],
    })


async def broadcast_waiting(runtime: RoomRuntime):
    # "Waiting for players to act" only exists in turn-based rooms. In an
    # exploration room nobody ever submits into pending_actions, so everyone
    # would look pending forever — never broadcast that.
    if not runtime.engine.turn_based:
        return
    pending = runtime.engine.room.players_pending()
    if pending:
        await broadcast(runtime, {
            "type": "waiting_for",
            "player_ids": pending,
        })


# --- per-room round timeout ----------------------------------------------------


def start_round_timeout(runtime: RoomRuntime):
    if runtime.timeout_task and not runtime.timeout_task.done():
        return
    runtime.timeout_task = asyncio.create_task(_round_timeout(runtime))


def cancel_round_timeout(runtime: RoomRuntime):
    if runtime.timeout_task and not runtime.timeout_task.done():
        runtime.timeout_task.cancel()
    runtime.timeout_task = None


async def _round_timeout(runtime: RoomRuntime):
    try:
        await asyncio.sleep(TURN_TIMEOUT)
        async with state_lock:
            # Staleness guard: this task may have been sleeping while its room
            # was evicted (and possibly reloaded fresh). A stale timer must
            # never force-resolve a room it doesn't own.
            if active_rooms.get(runtime.room_id) is not runtime:
                return
            # De-escalation guard (M7): the room may have calmed while this
            # timer slept (a disconnect auto-resolve killed the last hostile).
            # Exploration has no rounds to force — nobody is "pending".
            if not runtime.engine.turn_based:
                return
            if runtime.engine.room.players_pending():
                events = runtime.engine.force_resolve()
                cancel_round_timeout(runtime)
                await handle_round_events(runtime, events)
                await broadcast_state_and_events(runtime, events)
    except asyncio.CancelledError:
        pass


# --- traversal ------------------------------------------------------------------


async def handle_round_events(runtime: RoomRuntime, events) -> None:
    """Caller holds state_lock. React to domain events from a resolved round —
    currently just PLAYER_ENTERED_DOOR. Must run at EVERY resolution site
    (action submit, round timeout, disconnect auto-resolve), and must run
    BEFORE the old room's state broadcast so a traveling player never renders
    the old room's post-round state ahead of their room_changed message."""
    for event in events:
        if event.event_type is not EventType.PLAYER_ENTERED_DOOR:
            continue
        await _transfer_player(
            runtime,
            event.data["player_id"],
            event.data["to_room_id"],
        )
    await maybe_evict(runtime)


async def _transfer_player(origin: RoomRuntime, player_id: str, to_room_id: int) -> None:
    """Caller holds state_lock. Validate everything, THEN mutate — a failure
    at any check denies the traversal with zero state change."""
    player = origin.engine.room.get_player(player_id)
    if player is None or not player.is_alive:
        # The enemy phase runs after moves — the mover may have died on the
        # doorstep, or already left. Dead players don't traverse.
        return

    dest_was_loaded = to_room_id in active_rooms
    try:
        dest = await get_or_load_room(to_room_id)
    except Exception:
        logging.exception("failed to load room %s for traversal", to_room_id)
        await send_to(origin, player_id, {"type": "error", "message": "The way is blocked."})
        return

    if (len(dest.engine.room.players) >= dest.engine.room.template.capacity
            or dest.engine.room.free_spawn() is None):
        if not dest_was_loaded:
            await maybe_evict(dest)  # don't keep a room we loaded only to be denied
        await send_to(origin, player_id, {"type": "error", "message": "The way is blocked."})
        return

    # All checks passed — now mutate: detach from origin, rewire the socket,
    # attach at the destination.
    origin.engine.detach_player(player_id)
    ws = origin.connections.pop(player_id, None)
    arrival_events = dest.engine.attach_player(player)
    if ws is not None:
        dest.connections[player_id] = ws
    player_room[player_id] = to_room_id

    await send_to(dest, player_id, {
        "type": "room_changed",
        "state": dest.engine.get_state(),
        "events": [e.to_dict() for e in arrival_events],
    })
    await broadcast_state_and_events(dest, arrival_events)
    await broadcast_waiting(dest)


# --- NPC dialogue -----------------------------------------------------------------


async def handle_talk(websocket: WebSocket, player_id: str, data: dict) -> None:
    """Talking is a request, not an action (NPCS.md Decision 1): it lives
    outside the action economy in BOTH modes, never consumes a turn, never
    pauses the round timer — so it works mid-combat and cannot stall a round.

    Lock discipline: validate under state_lock, run the provider OUTSIDE it
    (never generate dialogue while holding the room lock — rounds keep
    resolving during a slow LLM call), then re-validate on re-entry: the
    room may have evicted or the NPC died while we awaited. Resolution-time
    state is authoritative, same as combat validation.
    """
    text = str(data.get("text", ""))[:TALK_TEXT_LIMIT].strip()
    npc_id = data.get("npc_id")

    async with state_lock:
        runtime = runtime_for(player_id)
        room = runtime.engine.room if runtime else None
        player = room.get_player(player_id) if room else None
        npc = room.get_entity(npc_id) if room and isinstance(npc_id, str) else None

        if not text or not player or not player.is_alive:
            reason = "Say something." if not text else "The dead do not speak."
            await websocket.send_json({"type": "error", "message": reason})
            return
        if not isinstance(npc, NPC) or not npc.is_alive:
            await websocket.send_json({"type": "error", "message": "There is nobody there to talk to."})
            return
        distance = abs(player.position.x - npc.position.x) + abs(player.position.y - npc.position.y)
        if distance != 1:
            # Same targeting rule as attack: walk adjacent, then interact.
            await websocket.send_json({"type": "error", "message": f"You are too far from {npc.name}."})
            return
        if player_id in talking_players:
            await websocket.send_json({"type": "error", "message": "You are already mid-sentence."})
            return
        talking_players.add(player_id)
        player_name = player.name

    try:
        reply = await dialogue_provider.reply(npc, player_name, text)
    finally:
        talking_players.discard(player_id)

    async with state_lock:
        # Re-entry through the validated path: only touch the NPC if it is
        # still the live object in a still-live room.
        current = active_rooms.get(runtime.room_id)
        if (current is not runtime
                or runtime.engine.room.npcs.get(npc.id) is not npc
                or not npc.is_alive):
            await websocket.send_json({"type": "error", "message": f"{npc.name} is no longer listening."})
            return
        npc.transcript.append({"speaker": player_name, "text": text})
        npc.transcript.append({"speaker": "npc", "text": reply.text})
        del npc.transcript[:-NPC_TRANSCRIPT_LIMIT]

        # Effect channel: the engine validates the LLM's raw proposals and
        # applies the accepted ones through the same apply_effect path combat
        # uses (invalid/unknown ones are dropped and logged inside). Runs under
        # the lock — pure CPU, no I/O — and its events are world-visible, so
        # they broadcast to the whole room, unlike the 1:1 dialogue text below.
        #
        # Re-fetch the player here: party effects (join_party) recruit THIS
        # owner, and the LLM call awaited outside the lock — the player may have
        # walked away or left. A missing owner just drops party proposals; the
        # spoken text still shows.
        owner = runtime.engine.room.get_player(player_id)
        effect_events = runtime.engine.apply_dialogue_effects(npc, reply.proposals, owner)

        # Timer work stays here (main owns timers): escalation needs none (the
        # first combat submission arms it, same as a fresh room), but a
        # de-escalating parley must cancel a mid-round timer or it would fire
        # into a room with no fight left (the engine already dropped the
        # half-collected round).
        if not runtime.engine.turn_based:
            cancel_round_timeout(runtime)
        if effect_events:
            await broadcast_state_and_events(runtime, effect_events)

    # Text channel: prose to the talking player only, always shown regardless
    # of whether any proposal was accepted — dialogue is one-on-one.
    await websocket.send_json({
        "type": "npc_dialogue",
        "npc_id": npc.id,
        "name": npc.name,
        "player_text": text,
        "text": reply.text,
    })


# --- loot: chests, packs, the world tick (docs/LOOT.md) --------------------------


async def world_ticker() -> None:
    """Sweep live rooms every tick: expire lapsed timed effects, then advance
    the hunger clock (drain / well-fed regen / starvation — backend/hunger.py
    owns what a tick means; this task only owns when it runs). One coarse
    global task, never per-effect timers (world_clock.py)."""
    while True:
        await asyncio.sleep(WORLD_TICK_INTERVAL)
        try:
            async with state_lock:
                for runtime in list(active_rooms.values()):
                    events = [
                        GameEvent(EventType.EFFECT_EXPIRED,
                                  {"target_id": actor.id, "stat": e["stat"],
                                   "amount": e["amount"], "source": e["source"]},
                                  runtime.engine.room.round)
                        for actor in runtime.engine.room.living_actors()
                        for e in prune_expired(actor)
                    ]
                    hunger_events, hunger_visible = tick_room_hunger(
                        runtime.engine.room, WORLD_TICK_INTERVAL)
                    events.extend(hunger_events)
                    if events or hunger_visible:
                        await broadcast_state_and_events(runtime, events)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The clock must keep ticking whatever one sweep hits.
            logging.exception("world ticker sweep failed")


def _adjacent_chest(room, player, object_id):
    """Validate 'this player can touch this chest right now'. Returns
    (chest, None) or (None, error message) — shared by both open paths."""
    if not player or not player.is_alive:
        return None, "The dead loot nothing."
    obj = room.get_object(object_id) if isinstance(object_id, str) else None
    if obj is None or obj.type != ObjectType.CHEST.value:
        return None, "There is no chest there."
    dx = abs(player.position.x - obj.position[0])
    dy = abs(player.position.y - obj.position[1])
    if dx + dy > 1:
        # Same rule as attack/talk: walk up to it, then interact.
        return None, "You are too far from the chest."
    return obj, None


async def handle_open_chest(websocket: WebSocket, player_id: str, data: dict) -> None:
    """Opening a chest is a REQUEST outside the action economy (the talk
    pattern, NPCS.md Decision 1): it needs the DB and maybe a premium LLM
    call, so it can't run inside synchronous round resolution — and standing
    at a chest mid-combat is its own punishment, so a free action is fair.

    First-to-open is decided under the lock by flipping `opened` BEFORE the
    slow roll; the roll itself runs outside the lock (rounds keep resolving
    during a slow LLM call), and the result lands under a re-validated lock
    — the handle_talk lock discipline, exactly.

    Nothing is auto-taken: the roll lands in `chest.contents` and the finds
    go back in the chest_opened broadcast, which the opener's client renders
    as the selection popup — take what you want, leave the rest
    (handle_take_item). Re-opening an already-opened chest just shows what
    still waits inside (the same popup, for anyone).
    """
    async with state_lock:
        runtime = runtime_for(player_id)
        room = runtime.engine.room if runtime else None
        player = room.get_player(player_id) if room else None
        chest, error = _adjacent_chest(room, player, data.get("object_id")) if room else (None, "Not in a room")
        if error:
            await websocket.send_json({"type": "error", "message": error})
            return

        if chest.opened:
            # Viewing, not claiming: show what still waits. 1:1 on purpose —
            # looking inside a chest is not a world-visible act.
            if not chest.contents:
                await websocket.send_json({"type": "error", "message": "The chest is empty."})
                return
            await websocket.send_json({
                "type": "chest_contents",
                "object_id": chest.id,
                "items": [{"item": item, "minted": False} for item in chest.contents],
            })
            return

        # The claim that decides first-to-open: later requests hit the branch
        # above (or "empty"), never a second roll.
        chest.opened = True

    # A chest holds 1-3 finds (weighted toward 1); each is its own
    # spawn_loot roll, so each independently gets the LLM-mint chance.
    rolled: list[tuple[dict, bool]] = []
    try:
        async with SessionMaker() as session:
            for _ in range(roll_item_count()):
                item, minted = await spawn_loot(session)
                if item is not None:
                    rolled.append((item, minted))
    except Exception:
        logging.exception("spawn_loot failed for chest %s", data.get("object_id"))

    async with state_lock:
        if active_rooms.get(runtime.room_id) is not runtime:
            # Room evicted mid-roll (everyone left) — the chest state is gone;
            # a minted item stays in the pool for future chests. Nothing to do.
            return
        if not rolled:
            # Roll failed entirely (empty pool / DB down): re-arm the chest so
            # the world never holds a permanently-eaten one.
            chest.opened = False
            await websocket.send_json({"type": "error", "message": "The latch refuses to budge."})
            return

        events = []
        finds = []
        for item, minted in rolled:
            if minted:
                events.append(GameEvent(
                    EventType.ITEM_GENERATED, {"item": item}, runtime.engine.room.round,
                ))
            # Everything lands IN the chest — taking is the player's choice,
            # made through take_item, never the server's.
            chest.contents.append(item)
            finds.append({"item": item, "minted": minted})
        events.append(GameEvent(
            EventType.CHEST_OPENED,
            {"player_id": player_id, "object_id": chest.id, "items": finds},
            runtime.engine.room.round,
        ))
        await broadcast_state_and_events(runtime, events)


async def handle_take_item(websocket: WebSocket, player_id: str, data: dict) -> None:
    """Take ONE chosen item out of an opened chest — the selection popup's
    Take button. The client sends the item's current `index` in the chest
    plus its `item_id` as a guard: contents shift as others take, so a stale
    click must fail loud ("already taken"), never grab the wrong thing."""
    async with state_lock:
        runtime = runtime_for(player_id)
        room = runtime.engine.room if runtime else None
        player = room.get_player(player_id) if room else None
        chest, error = _adjacent_chest(room, player, data.get("object_id")) if room else (None, "Not in a room")
        if error:
            await websocket.send_json({"type": "error", "message": error})
            return
        if not chest.opened:
            await websocket.send_json({"type": "error", "message": "The chest is still shut."})
            return
        index = data.get("index")
        if (not isinstance(index, int) or not (0 <= index < len(chest.contents))
                or chest.contents[index].get("id") != data.get("item_id")):
            await websocket.send_json({"type": "error", "message": "That's already been taken."})
            return
        if add_item(player, chest.contents[index]) is None:
            await websocket.send_json({"type": "error", "message": "Your pack is full."})
            return
        item = chest.contents.pop(index)
        await broadcast_state_and_events(runtime, [GameEvent(
            EventType.CHEST_LOOTED,
            {"player_id": player_id, "object_id": chest.id, "item": item},
            room.round,
        )])


async def handle_equip_toggle(websocket: WebSocket, player_id: str, data: dict,
                              *, equipping: bool) -> None:
    """Equip/unequip slot N — free actions outside the round economy (gear
    fiddling is instant; revisit if mid-combat armor swapping gets abusive).
    All rules live in backend/inventory.py; this is transport."""
    async with state_lock:
        runtime = runtime_for(player_id)
        player = runtime.engine.room.get_player(player_id) if runtime else None
        if player is None or not player.is_alive:
            await websocket.send_json({"type": "error", "message": "You can't do that now."})
            return
        slot = data.get("slot")
        error = equip(player, slot) if equipping else unequip(player, slot)
        if error:
            await websocket.send_json({"type": "error", "message": error})
            return
        held = player.inventory[slot]["item"]
        await broadcast_state_and_events(runtime, [GameEvent(
            EventType.ITEM_EQUIPPED if equipping else EventType.ITEM_UNEQUIPPED,
            {"player_id": player_id, "slot": slot, "item": held},
            runtime.engine.room.round,
        )])


# --- dev affordances ------------------------------------------------------------


async def handle_dev_reset(websocket: WebSocket) -> None:
    """DEV-only (gated by DEV_MODE): discard every live room and restore the
    world to its seeded starting state — individuals reset (a living Mara, a
    neutral Gorrik), and fungible enemies respawn when rooms reload. This boots
    everyone, so it is a testing affordance, never a shipped feature.

    Current state is DISCARDED, not saved: we skip _save_individuals on purpose
    (the whole point is to throw away the played-in state), then tell every
    connected client to reload into the fresh world.
    """
    if not DEV_MODE:
        await websocket.send_json({"type": "error", "message": "Dev reset is disabled."})
        return

    async with state_lock:
        # Capture sockets BEFORE clearing the registry (dedup by identity, and
        # include the caller in case they hadn't joined a room yet).
        sockets = {id(ws): ws for rt in active_rooms.values() for ws in rt.connections.values()}
        sockets[id(websocket)] = websocket

        for runtime in list(active_rooms.values()):
            cancel_round_timeout(runtime)
        active_rooms.clear()
        player_room.clear()

        async with SessionMaker() as session:
            await reset_npcs(session)

    # Clients reload and reconnect fresh — a full reload sidesteps any partial
    # state left on the old sockets.
    for ws in sockets.values():
        try:
            await ws.send_json({"type": "world_reset"})
        except Exception:
            pass


# --- websocket endpoint ---------------------------------------------------------


def runtime_for(player_id: str) -> RoomRuntime | None:
    """Caller holds state_lock. 'What room is this session running?'"""
    room_id = player_room.get(player_id)
    if room_id is None:
        return None
    return active_rooms.get(room_id)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    player_id = None

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "join":
                # Token-in-first-message (ACCOUNTS.md Decision 5): the token
                # resolves to a player row BEFORE the socket joins any room.
                # Auth failures carry code "auth" so the client knows to drop
                # its stored token and show the login form again.
                if player_id:
                    await websocket.send_json({"type": "error", "message": "Already joined."})
                    continue

                token = data.get("token")
                account_id = auth.verify_token(token) if isinstance(token, str) else None
                if account_id is None:
                    await websocket.send_json({
                        "type": "error", "code": "auth",
                        "message": "Invalid session — please log in.",
                    })
                    continue

                async with SessionMaker() as session:
                    row = await get_player_row(session, account_id)
                if row is None:
                    # A validly signed token for a vanished row (db reset):
                    # same remedy as a forged one — log in again.
                    await websocket.send_json({
                        "type": "error", "code": "auth",
                        "message": "Unknown account — please register again.",
                    })
                    continue

                async with state_lock:
                    # One socket per account (Decision 6): reject the newcomer.
                    # Revisit trigger: if refresh-during-play feels broken,
                    # switch to newest-connection-takes-over.
                    if account_id in player_room:
                        await websocket.send_json({
                            "type": "error",
                            "message": "This account is already playing — one connection at a time.",
                        })
                        continue

                    # Where to resume (ACCOUNTS.md flow). A character saved
                    # dead respawns fresh at the default room; a saved room
                    # that no longer loads falls back the same way.
                    respawning = row.hp <= 0
                    target_room_id = row.room_id
                    preferred = None
                    if respawning or target_room_id is None:
                        target_room_id = default_room_id
                    elif row.x is not None and row.y is not None:
                        preferred = Position(row.x, row.y)

                    try:
                        runtime = await get_or_load_room(target_room_id)
                    except Exception:
                        logging.exception("failed to load saved room %s", target_room_id)
                        if target_room_id == default_room_id:
                            await websocket.send_json({"type": "error", "message": "The world failed to load."})
                            continue
                        target_room_id, preferred = default_room_id, None
                        runtime = await get_or_load_room(target_room_id)

                    player = make_live_player(row)
                    try:
                        events = runtime.engine.attach_player(player, preferred)
                    except ValueError:
                        await maybe_evict(runtime)  # don't leak a speculatively loaded room
                        await websocket.send_json({
                            "type": "error",
                            "message": "The room is full — try again in a moment.",
                        })
                        continue

                    player_id = player.id
                    runtime.connections[player_id] = websocket
                    player_room[player_id] = runtime.room_id

                    await send_to(runtime, player_id, {
                        "type": "join_ack",
                        "player_id": player_id,
                        "username": row.username,
                        "state": runtime.engine.get_state(),
                    })

                    await broadcast_state_and_events(runtime, events)

            elif msg_type == "action":
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "Join first"})
                    continue

                async with state_lock:
                    runtime = runtime_for(player_id)
                    if runtime is None:
                        await websocket.send_json({"type": "error", "message": "Not in a room"})
                        continue

                    events, resolved = runtime.engine.submit_action(player_id, data)

                    has_error = any(e.event_type.value == "invalid_action" for e in events)
                    if has_error:
                        await send_to(runtime, player_id, {
                            "type": "error",
                            "message": events[0].data.get("reason", "Invalid action"),
                        })
                    elif resolved:
                        cancel_round_timeout(runtime)
                        # Transfers first: travelers' sockets leave this room's
                        # connections, so the state broadcast below reaches
                        # only the players still here.
                        await handle_round_events(runtime, events)
                        await broadcast_state_and_events(runtime, events)
                    else:
                        await send_to(runtime, player_id, {"type": "action_locked"})
                        start_round_timeout(runtime)
                        await broadcast_waiting(runtime)

            elif msg_type == "talk":
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "Join first"})
                    continue
                await handle_talk(websocket, player_id, data)

            elif msg_type == "open_chest":
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "Join first"})
                    continue
                await handle_open_chest(websocket, player_id, data)

            elif msg_type == "take_item":
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "Join first"})
                    continue
                await handle_take_item(websocket, player_id, data)

            elif msg_type in ("equip", "unequip"):
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "Join first"})
                    continue
                await handle_equip_toggle(websocket, player_id, data,
                                          equipping=(msg_type == "equip"))

            elif msg_type == "dev_reset":
                # World-wide reset — deliberately does NOT require a player_id
                # (usable from the join screen too). Gated by DEV_MODE inside.
                await handle_dev_reset(websocket)

            elif msg_type == "inspect_object":
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "Join first"})
                    continue

                async with state_lock:
                    runtime = runtime_for(player_id)
                    obj = runtime.engine.room.get_object(data.get("object_id")) if runtime else None

                if not obj:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Object not found",
                    })
                    continue

                await websocket.send_json({
                    "type": "object_inspection",
                    "object": obj.to_dict(),
                })

    except WebSocketDisconnect:
        pass
    except Exception:
        logging.exception("websocket error")
    finally:
        if player_id:
            async with state_lock:
                runtime = runtime_for(player_id)
                player_room.pop(player_id, None)
                if runtime is not None:
                    runtime.connections.pop(player_id, None)
                    # Persistence at the disconnect edge (ACCOUNTS.md
                    # Decision 7): capture the leaver BEFORE removal — their
                    # state is final now; the auto-resolve below only moves
                    # the players who stayed.
                    leaver = runtime.engine.room.get_player(player_id)
                    if leaver is not None:
                        await _save_player(leaver, runtime.room_id)
                    # remove_player may auto-resolve the round (the leaver was
                    # the last pending player) — that resolution can contain
                    # door events for OTHER players, so this site handles
                    # traversal too.
                    events, _ = runtime.engine.remove_player(player_id)
                    await handle_round_events(runtime, events)
                    if events:
                        await broadcast_state_and_events(runtime, events)
                    await maybe_evict(runtime)
