"""M8 accounts (ACCOUNTS.md): identity, auth, resume, and the one-socket rule.

Three layers, mirroring how the feature is built:
  - auth primitives (no DB): hashing and token signing.
  - player_store + engine (session fixture): register/authenticate/save/load,
    preferred-spawn placement, follower rebinding.
  - the full HTTP+WS loop (disposable file DB + TestClient): register, join
    with the token, disconnect, log in again into the same spot.
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import auth
from backend.db import Base
from backend.entities import Position
from backend.models import NPCRow, PlayerRow
from backend.npc_store import load_npcs
from backend.player_store import (
    UsernameTaken,
    authenticate,
    get_player_row,
    make_live_player,
    register_player,
    save_players,
)
from backend.room_engine import RoomEngine
from backend.seeds import get_or_seed_default_room


# --- auth primitives ----------------------------------------------------------


def test_password_hash_roundtrip():
    hashed = auth.hash_password("hunter22")
    assert hashed != "hunter22"                      # never stored raw
    assert hashed.startswith("$2")                   # bcrypt format
    assert auth.verify_password("hunter22", hashed)
    assert not auth.verify_password("wrong", hashed)


def test_verify_password_survives_malformed_hash():
    # A hand-edited row must read as "wrong password", not a 500.
    assert not auth.verify_password("x", "not-a-bcrypt-hash")


def test_token_roundtrip_and_tamper():
    token = auth.sign_token("player_abc123")
    assert auth.verify_token(token) == "player_abc123"

    # Any modification kills the signature.
    assert auth.verify_token(token[:-1]) is None
    assert auth.verify_token("player_evil." + token.split(".")[1]) is None
    assert auth.verify_token("garbage") is None
    assert auth.verify_token("") is None


# --- player_store -------------------------------------------------------------


async def test_register_stores_hash_not_password(session):
    row = await register_player(session, "mara", "secret-pass", "m@example.com")
    assert row.id.startswith("player_")             # get_entity prefix dispatch
    assert row.password_hash != "secret-pass"
    assert "secret-pass" not in row.password_hash
    assert row.email == "m@example.com"
    assert row.room_id is None                      # not placed yet
    assert row.coins == 30


async def test_duplicate_username_rejected(session):
    await register_player(session, "mara", "first")
    with pytest.raises(UsernameTaken):
        await register_player(session, "mara", "second")


async def test_authenticate(session):
    row = await register_player(session, "gorrik", "axes4ever")
    assert (await authenticate(session, "gorrik", "axes4ever")).id == row.id
    assert await authenticate(session, "gorrik", "wrong") is None
    assert await authenticate(session, "nobody", "axes4ever") is None


async def test_dead_character_respawns_fresh(session):
    row = await register_player(session, "lazarus", "password")
    row.hp = 0
    player = make_live_player(row)
    assert player.hp == player.max_hp


async def test_save_players_roundtrip(session, make_template):
    row = await register_player(session, "wanderer", "password")
    engine = RoomEngine(make_template())
    player = make_live_player(row)
    engine.attach_player(player)
    player.position = Position(2, 3)
    player.hp = 41
    player.coins = 17

    await save_players(session, [player], room_id=1)

    saved = await get_player_row(session, row.id)
    assert (saved.room_id, saved.x, saved.y, saved.hp) == (1, 2, 3, 41)
    assert saved.coins == 17


async def test_save_players_skips_rowless_entities(session, make_template):
    # Engine-made players (tests, pre-account paths) have counter ids with no
    # row — saving a room containing one must not blow up.
    engine = RoomEngine(make_template())
    player, _ = engine.join("Ephemeral")
    await save_players(session, [player], room_id=1)


# --- engine placement on login -------------------------------------------------


def test_attach_prefers_saved_position(make_template):
    engine = RoomEngine(make_template())
    row = PlayerRow(id="player_x", username="x", password_hash="h", hp=50)
    row.x, row.y = 3, 3
    player = make_live_player(row)
    engine.attach_player(player, preferred=Position(3, 3))
    assert (player.position.x, player.position.y) == (3, 3)
    assert player.hp == 50                          # same hp as saved


def test_attach_falls_back_when_preferred_blocked(make_template):
    engine = RoomEngine(make_template())
    squatter, _ = engine.join("Squatter")
    engine.room.move_entity(squatter.id, Position(3, 3))

    row = PlayerRow(id="player_y", username="y", password_hash="h", hp=50)
    player = make_live_player(row)
    engine.attach_player(player, preferred=Position(3, 3))
    # Occupied preferred tile downgrades to a free spawn, never an error.
    assert (player.position.x, player.position.y) != (3, 3)
    assert player.id in engine.room.players


def test_attach_falls_back_when_preferred_is_wall(make_template):
    engine = RoomEngine(make_template())
    row = PlayerRow(id="player_z", username="z", password_hash="h", hp=50)
    player = make_live_player(row)
    engine.attach_player(player, preferred=Position(0, 0))   # border wall
    assert (player.position.x, player.position.y) != (0, 0)


# --- follower rebinding (ACCOUNTS.md DoD) ---------------------------------------


async def test_follower_rebinds_to_account_across_loads(session):
    """A recruited follower stores the ACCOUNT id, which is stable across
    sessions and restarts — so reloading the room rebinds it to the same
    owner. (Pre-M8 this broke: runtime counter ids changed every connect.)"""
    owner = await register_player(session, "captain", "password")
    persona = {
        "id": "sellsword", "name": "Sellsword", "role": "mercenary",
        "persona": "A blade for hire.", "drives": ["coin"],
        "party_policy": "recruitable", "canned": ["Hm."],
        "disposition": "friendly",
    }
    session.add(NPCRow(
        room_id=1, name="Sellsword", x=2, y=2, hp=10, max_hp=10,
        persona=persona, memory=[], party_owner_id=owner.id,
    ))
    await session.commit()

    # Simulate the next session: load the room's individuals fresh.
    npcs = await load_npcs(session, room_id=1)
    assert npcs[0].party_owner_id == owner.id
    # And the id a returning login gets IS that same id.
    returning = await authenticate(session, "captain", "password")
    assert returning.id == owner.id


# --- full HTTP + WS loop ---------------------------------------------------------


@pytest.fixture
def game_app(tmp_path, monkeypatch):
    """backend.main wired to a disposable file DB. File-based (not :memory:)
    because the TestClient drives the app from its own event loop — each loop
    needs its own connections, which a shared in-memory DB can't give."""
    import backend.main as main

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def setup() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as s:
            room = await get_or_seed_default_room(s)
            return room.id

    room_id = asyncio.run(setup())
    monkeypatch.setattr(main, "SessionMaker", maker)
    monkeypatch.setattr(main, "default_room_id", room_id)
    main.active_rooms.clear()
    main.player_room.clear()
    yield main
    main.active_rooms.clear()
    main.player_room.clear()
    # No engine.dispose(): the TestClient's loop owns the connections and is
    # gone by teardown; the tmp_path file dies with the test anyway.


def _drain_disconnect(main_module):
    """Wait until the server finished its disconnect cleanup (eviction is its
    last step). Exiting the TestClient's `with` block cancels the handler task
    mid-save — a harness artifact, not a server behavior — so tests close the
    socket explicitly and wait instead."""
    deadline = time.time() + 2.0
    while main_module.active_rooms or main_module.player_room:
        assert time.time() < deadline, "server never finished disconnect cleanup"
        time.sleep(0.01)


def test_register_login_http(game_app):
    client = TestClient(game_app.app)

    res = client.post("/register", json={"username": "hero", "password": "swordfish"})
    assert res.status_code == 201
    token = res.json()["token"]
    assert auth.verify_token(token) == res.json()["player_id"]

    assert client.post("/register", json={"username": "hero", "password": "otherpass"}).status_code == 409
    assert client.post("/register", json={"username": "x", "password": "swordfish"}).status_code == 400
    assert client.post("/register", json={"username": "shorty", "password": "abc"}).status_code == 400

    assert client.post("/login", json={"username": "hero", "password": "swordfish"}).status_code == 200
    assert client.post("/login", json={"username": "hero", "password": "wrong"}).status_code == 401
    assert client.post("/login", json={"username": "ghost", "password": "swordfish"}).status_code == 401


def test_join_resume_and_single_connection(game_app):
    client = TestClient(game_app.app)
    token = client.post(
        "/register", json={"username": "traveler", "password": "swordfish"}
    ).json()["token"]

    # No/invalid token cannot join a room (DoD).
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "token": "forged.token"})
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["code"] == "auth"

    # First login: lands in the default room at a spawn point.
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "token": token})
        ack = ws.receive_json()
        assert ack["type"] == "join_ack"
        pid = ack["player_id"]
        assert ack["username"] == "traveler"
        first_pos = ack["state"]["players"][pid]["position"]
        first_hp = ack["state"]["players"][pid]["hp"]

        # Second socket for the same account is refused (DoD) while playing.
        with client.websocket_connect("/ws") as ws2:
            ws2.send_json({"type": "join", "token": token})
            msg = ws2.receive_json()
            assert msg["type"] == "error"
            assert "already playing" in msg["message"]

        ws.close()
        _drain_disconnect(game_app)

    # Disconnect saved the row; logging in again resumes the same identity,
    # room, position, and hp (DoD) — across an eviction+reload of the room.
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "token": token})
        ack = ws.receive_json()
        assert ack["type"] == "join_ack"
        assert ack["player_id"] == pid
        assert ack["state"]["players"][pid]["position"] == first_pos
        assert ack["state"]["players"][pid]["hp"] == first_hp
