"""Accounts at rest <-> live Players (ACCOUNTS.md M8).

The players twin of npc_store: registration and login read/write the auth
columns over HTTP; the WebSocket layer turns a row into a live Player entity
on join and writes position/hp back at the edges — disconnect and shutdown —
never in the hot loop (Decision 7).
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend import auth
from backend.config import PLAYER_MAX_HP, PLAYER_STARTING_COINS
from backend.entities import Player, Position
from backend.models import PlayerRow


class UsernameTaken(Exception):
    """Registration collided with an existing username."""


async def register_player(
    session: AsyncSession, username: str, password: str, email: str | None = None
) -> PlayerRow:
    """Create an account with a full-hp, not-yet-placed character. The
    password crosses this function boundary exactly once and leaves only as a
    bcrypt hash (hashed in a worker thread — see auth.hash_password)."""
    password_hash = await asyncio.to_thread(auth.hash_password, password)
    row = PlayerRow(
        id=f"player_{uuid.uuid4().hex}",
        username=username,
        password_hash=password_hash,
        email=email or None,
        hp=PLAYER_MAX_HP,
        coins=PLAYER_STARTING_COINS,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # The username's unique index is the real duplicate gate — a
        # pre-check SELECT would still race a concurrent register, so we
        # skip it and let the constraint answer atomically.
        await session.rollback()
        raise UsernameTaken(username)
    return row


async def authenticate(
    session: AsyncSession, username: str, password: str
) -> PlayerRow | None:
    """The account row if the credentials match, else None. One return shape
    for both "no such user" and "wrong password" so /login can't leak which
    usernames exist. (Response TIMING still can — a miss skips the bcrypt
    check — accepted until ACCOUNTS.md's rate-limiting trigger fires.)"""
    row = (await session.execute(
        select(PlayerRow).where(PlayerRow.username == username)
    )).scalar_one_or_none()
    if row is None:
        return None
    ok = await asyncio.to_thread(auth.verify_password, password, row.password_hash)
    return row if ok else None


async def get_player_row(session: AsyncSession, player_id: str) -> PlayerRow | None:
    return await session.get(PlayerRow, player_id)


def make_live_player(row: PlayerRow) -> Player:
    """Row -> live entity. The position here is a placeholder — the engine
    assigns the real tile on attach (saved position is passed separately as
    the *preferred* spawn). A character saved dead respawns fresh: hp<=0 must
    never enter play, so it comes back at full health (and the caller sends
    it to the default room)."""
    respawning = row.hp <= 0
    hp = row.hp if not respawning else PLAYER_MAX_HP
    from backend.config import HUNGER_MAX
    saved_hunger = row.hunger if row.hunger is not None else HUNGER_MAX
    return Player(
        id=row.id,
        name=row.username,
        position=Position(0, 0),
        hp=hp,
        max_hp=PLAYER_MAX_HP,
        # The pack survives death and disconnect alike (dying costs you your
        # position, not your stuff — revisit if drops-on-death becomes a
        # design goal). list() guards the ORM's mutable JSON default.
        inventory=list(row.inventory or []),
        # Death resets the belly with the body; a survivor resumes as hungry
        # as they left (hunger only drains while connected — offline time is
        # free, docs/LOOT.md Decision 5).
        hunger=float(HUNGER_MAX) if respawning else float(saved_hunger),
        coins=max(0, int(row.coins if row.coins is not None else PLAYER_STARTING_COINS)),
    )


async def save_players(
    session: AsyncSession, players: list[Player], room_id: int
) -> None:
    """Write live state back to rows — the npc_store rhythm: one commit for
    the batch. Callers pass the players of ONE room (that's the unit every
    save site owns). Rows are matched by id; entities without a row (engine
    tests use counter ids) are skipped, so a stray one can't abort the rest."""
    for player in players:
        row = await session.get(PlayerRow, player.id)
        if row is None:
            continue
        row.room_id = room_id
        row.x = player.position.x
        row.y = player.position.y
        row.hp = max(player.hp, 0)
        row.hunger = max(player.hunger, 0.0)
        row.coins = max(player.coins, 0)
        # Whole-column swap, never in-place mutation: SQLAlchemy only sees
        # JSON changes when the attribute is REASSIGNED. active_effects are
        # deliberately not saved — a buff is session-scoped, gear is forever.
        row.inventory = list(player.inventory)
    await session.commit()
