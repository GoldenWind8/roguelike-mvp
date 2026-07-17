"""Password hashing and session tokens (ACCOUNTS.md M8).

Two small primitives, no auth framework:

  - Passwords: bcrypt (Decision 3) — one battle-tested dependency. hashpw is
    deliberately slow (~100ms of CPU), which is the point against offline
    cracking but poison on the event loop: async callers must run these
    helpers through asyncio.to_thread (player_store does).

  - Session tokens (Decision 5): "<player_id>.<hmac-sha256-signature>",
    signed with SECRET_KEY. itsdangerous-style, hand-rolled because the whole
    scheme is ten lines of stdlib (same call as config's dotenv parsing).
    No expiry and no revocation — tokens are long-lived by design until the
    "accounts guard anything worth stealing" trigger fires (see Deferred).
"""
import base64
import hashlib
import hmac

import bcrypt

from backend.config import SECRET_KEY


def hash_password(password: str) -> str:
    # gensalt() embeds a random salt in the hash, so equal passwords still
    # produce different hashes — checkpw reads the salt back out to verify.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        # A malformed stored hash (hand-edited row) must read as "wrong
        # password", never as a 500 on the login endpoint.
        return False


def _signature(player_id: str) -> str:
    digest = hmac.new(SECRET_KEY.encode(), player_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def sign_token(player_id: str) -> str:
    return f"{player_id}.{_signature(player_id)}"


def verify_token(token: str) -> str | None:
    """The signed-in player's id, or None for anything malformed or forged.
    Splits on the LAST dot so the scheme survives ids that ever contain one."""
    player_id, sep, signature = token.rpartition(".")
    if not sep or not player_id:
        return None
    # compare_digest, not ==: constant-time comparison, so an attacker can't
    # learn a valid signature byte-by-byte from response timing.
    if not hmac.compare_digest(signature, _signature(player_id)):
        return None
    return player_id
