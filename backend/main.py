import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from backend.game import Game

app = FastAPI()
game = Game()
connections: dict[str, WebSocket] = {}
game_lock = asyncio.Lock()

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

                    await broadcast({
                        "type": "state_update",
                        "state": game.get_state(),
                        "events": [e.to_dict() for e in events],
                    })

            elif msg_type == "action":
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "Join first"})
                    continue

                async with game_lock:
                    events = game.submit_action(player_id, data)

                    has_error = any(e.event_type.value == "invalid_action" for e in events)
                    if has_error:
                        await send_to(player_id, {
                            "type": "error",
                            "message": events[0].data.get("reason", "Invalid action"),
                        })
                    else:
                        await broadcast({
                            "type": "state_update",
                            "state": game.get_state(),
                            "events": [e.to_dict() for e in events],
                        })

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if player_id:
            connections.pop(player_id, None)
            async with game_lock:
                events = game.remove_player(player_id)
            if events:
                await broadcast({
                    "type": "state_update",
                    "state": game.get_state(),
                    "events": [e.to_dict() for e in events],
                })
