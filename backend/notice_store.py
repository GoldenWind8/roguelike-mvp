"""Persistent player notices layered below authored noticeboard content."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import NoticePost
from backend.noticeboard_defs import NoticeboardDefinition


NOTICE_TEXT_LIMIT = 500


class NoticeError(Exception):
    """A safe, player-facing noticeboard refusal."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _authored_view(definition: NoticeboardDefinition) -> list[dict]:
    return [
        {
            "id": f"authored:{notice.id}",
            "kind": "authored",
            "author": notice.author,
            "body": notice.body,
            "posted_at": None,
            "expires_at": None,
            "can_delete": False,
        }
        for notice in definition.notices
    ]


def _player_view(row: NoticePost, viewer_id: str) -> dict:
    return {
        "id": f"player:{row.id}",
        "kind": "player",
        "author": row.author_name,
        "body": row.body,
        "posted_at": _iso(row.created_at),
        "expires_at": _iso(row.expires_at),
        "can_delete": row.author_player_id == viewer_id,
    }


async def _prune_expired(
    session: AsyncSession, board_id: str, now: datetime,
) -> None:
    await session.execute(
        delete(NoticePost).where(
            NoticePost.board_id == board_id,
            NoticePost.expires_at <= now,
        )
    )


async def list_notices(
    session: AsyncSession,
    definition: NoticeboardDefinition,
    viewer_id: str,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Return authored notices followed by newest active player posts."""
    now = now or utc_now()
    await _prune_expired(session, definition.id, now)
    await session.commit()
    rows = (await session.execute(
        select(NoticePost)
        .where(
            NoticePost.board_id == definition.id,
            NoticePost.expires_at > now,
        )
        .order_by(NoticePost.created_at.desc(), NoticePost.id.desc())
    )).scalars().all()
    return _authored_view(definition) + [_player_view(row, viewer_id) for row in rows]


async def post_notice(
    session: AsyncSession,
    definition: NoticeboardDefinition,
    *,
    player_id: str,
    author_name: str,
    body: str,
    now: datetime | None = None,
) -> NoticePost:
    """Post one expiring message; each player may hold one slot per board."""
    if not isinstance(body, str):
        raise NoticeError("Write a message before pinning the notice.")
    clean = body.strip()
    if not clean:
        raise NoticeError("Write a message before pinning the notice.")
    if len(clean) > NOTICE_TEXT_LIMIT:
        raise NoticeError(f"Notices may be at most {NOTICE_TEXT_LIMIT} characters.")

    now = now or utc_now()
    await _prune_expired(session, definition.id, now)
    # Expiry is shared-world cleanup in its own right. Commit it before
    # applying validation so a rejected new post cannot resurrect old rows.
    await session.commit()
    existing = (await session.execute(
        select(NoticePost.id).where(
            NoticePost.board_id == definition.id,
            NoticePost.author_player_id == player_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise NoticeError("You already have a notice here. Take it down before posting another.")

    count = (await session.execute(
        select(func.count()).select_from(NoticePost).where(
            NoticePost.board_id == definition.id,
        )
    )).scalar_one()
    if count >= definition.max_player_posts:
        raise NoticeError("There is no room left on the board.")

    row = NoticePost(
        board_id=definition.id,
        author_player_id=player_id,
        author_name=author_name,
        body=clean,
        created_at=now,
        expires_at=now + timedelta(days=definition.post_ttl_days),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise NoticeError(
            "You already have a notice here. Take it down before posting another."
        ) from exc
    return row


async def delete_notice(
    session: AsyncSession,
    definition: NoticeboardDefinition,
    *,
    player_id: str,
    notice_id: int,
) -> None:
    if not isinstance(notice_id, int):
        raise NoticeError("That notice is no longer here.")
    row = await session.get(NoticePost, notice_id, with_for_update=True)
    if row is None or row.board_id != definition.id:
        raise NoticeError("That notice is no longer here.")
    if row.author_player_id != player_id:
        raise NoticeError("You can only take down your own notice.")
    await session.delete(row)
    await session.commit()
