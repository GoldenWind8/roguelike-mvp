import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from backend.config import NPC_TRANSCRIPT_LIMIT, TALK_TEXT_LIMIT, TURN_TIMEOUT
from backend.db import SessionMaker, init_db
from backend.dialogue import build_provider
from backend.entities import NPC
from backend.events import EventType
from backend.npc_store import load_npcs, save_npcs
from backend.room_engine import RoomEngine
from backend.room_loader import load_room
from backend.seeds import get_or_seed_default_room


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
    yield
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

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/game.js")
async def serve_js():
    return FileResponse(FRONTEND_DIR / "game.js", media_type="application/javascript")


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
        active_rooms[room_id] = runtime
    return runtime


async def _save_individuals(runtime: RoomRuntime) -> None:
    npcs = list(runtime.engine.room.npcs.values())
    if not npcs:
        return
    try:
        async with SessionMaker() as session:
            await save_npcs(session, npcs)
    except Exception:
        # A failed save must not take the room registry down with it — the
        # in-memory state is still authoritative until the next save point.
        logging.exception("failed to save NPCs for room %s", runtime.room_id)


async def maybe_evict(runtime: RoomRuntime) -> None:
    """Caller holds state_lock. Drop an empty room from the registry.
    Eviction is "save individuals, unload" (NPCS.md Decision 10): NPC rows
    are written back first; fungible enemy state is deliberately forgotten —
    the next visit reseeds enemies from the template (respawn is a feature)
    and reloads individuals from their rows."""
    if runtime.engine.room.players:
        return
    await _save_individuals(runtime)
    cancel_round_timeout(runtime)
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
        npc.transcript.append({"speaker": "npc", "text": reply})
        del npc.transcript[:-NPC_TRANSCRIPT_LIMIT]

    # Text channel only (M4): prose to the talking player, no state change,
    # no broadcast — dialogue is one-on-one.
    await websocket.send_json({
        "type": "npc_dialogue",
        "npc_id": npc.id,
        "name": npc.name,
        "player_text": text,
        "text": reply,
    })


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
                async with state_lock:
                    runtime = await get_or_load_room(default_room_id)
                    try:
                        player, events = runtime.engine.join(data.get("name", "Anonymous"))
                    except ValueError as e:
                        await maybe_evict(runtime)  # don't leak a speculatively loaded room
                        await websocket.send_json({"type": "error", "message": str(e)})
                        continue

                    player_id = player.id
                    runtime.connections[player_id] = websocket
                    player_room[player_id] = runtime.room_id

                    await send_to(runtime, player_id, {
                        "type": "join_ack",
                        "player_id": player_id,
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
                    # remove_player may auto-resolve the round (the leaver was
                    # the last pending player) — that resolution can contain
                    # door events for OTHER players, so this site handles
                    # traversal too.
                    events, _ = runtime.engine.remove_player(player_id)
                    await handle_round_events(runtime, events)
                    if events:
                        await broadcast_state_and_events(runtime, events)
                    await maybe_evict(runtime)
