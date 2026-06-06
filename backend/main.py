import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from backend.config import TURN_TIMEOUT
from backend.db import SessionMaker, init_db
from backend.game import Game
from backend.level_loader import load_level
from backend.levels import get_or_seed_default_room


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build db tables, then seed-if-empty and load the starting room from the DB
    # into the in-memory Game (the level is no longer a hardcoded constant).
    # create_all is idempotent (checkfirst=True), as is get_or_seed_default_room.
    global game
    await init_db()
    async with SessionMaker() as session:
        room = await get_or_seed_default_room(session)
        level = await load_level(session, room.id)
    game = Game(level)
    yield


app = FastAPI(lifespan=lifespan)
# Built in lifespan once the starting room is loaded from the DB.
game: Game | None = None
connections: dict[str, WebSocket] = {}
game_lock = asyncio.Lock()
round_timeout_task: asyncio.Task | None = None

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/game.js")
async def serve_js():
    return FileResponse(FRONTEND_DIR / "game.js", media_type="application/javascript")


async def broadcast(message: dict):
    disconnected = []
    for pid, ws in connections.items():
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(pid)
    for pid in disconnected:
        connections.pop(pid, None)


async def send_to(player_id: str, message: dict):
    ws = connections.get(player_id)
    if ws:
        try:
            await ws.send_json(message)
        except Exception:
            connections.pop(player_id, None)


async def broadcast_state_and_events(events):
    await broadcast({
        "type": "state_update",
        "state": game.get_state(),
        "events": [e.to_dict() for e in events],
    })


async def broadcast_waiting():
    pending = game.world.players_pending()
    if pending:
        await broadcast({
            "type": "waiting_for",
            "player_ids": pending,
        })


def start_round_timeout():
    global round_timeout_task
    if round_timeout_task and not round_timeout_task.done():
        return
    round_timeout_task = asyncio.create_task(_round_timeout())


def cancel_round_timeout():
    global round_timeout_task
    if round_timeout_task and not round_timeout_task.done():
        round_timeout_task.cancel()
    round_timeout_task = None


async def _round_timeout():
    try:
        await asyncio.sleep(TURN_TIMEOUT)
        async with game_lock:
            if game.world.players_pending():
                events = game.force_resolve()
                cancel_round_timeout()
                await broadcast_state_and_events(events)
    except asyncio.CancelledError:
        pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    player_id = None

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "join":
                async with game_lock:
                    try:
                        player, events = game.join(data.get("name", "Anonymous"))
                    except ValueError as e:
                        await websocket.send_json({"type": "error", "message": str(e)})
                        continue

                    player_id = player.id
                    connections[player_id] = websocket

                    await send_to(player_id, {
                        "type": "join_ack",
                        "player_id": player_id,
                        "state": game.get_state(),
                    })

                    await broadcast_state_and_events(events)

            elif msg_type == "action":
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "Join first"})
                    continue

                async with game_lock:
                    events, round_resolved = game.submit_action(player_id, data)

                    has_error = any(e.event_type.value == "invalid_action" for e in events)
                    if has_error:
                        await send_to(player_id, {
                            "type": "error",
                            "message": events[0].data.get("reason", "Invalid action"),
                        })
                    elif round_resolved:
                        cancel_round_timeout()
                        await broadcast_state_and_events(events)
                    else:
                        await send_to(player_id, {"type": "action_locked"})
                        start_round_timeout()
                        await broadcast_waiting()

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if player_id:
            connections.pop(player_id, None)
            async with game_lock:
                events, round_resolved = game.remove_player(player_id)
            if events:
                await broadcast_state_and_events(events)
