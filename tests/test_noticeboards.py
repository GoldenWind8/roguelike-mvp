"""Authored noticeboards plus constrained, expiring player messages."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.notice_store import (
    NoticeError,
    delete_notice,
    list_notices,
    post_notice,
)
from backend.noticeboard_defs import get_noticeboard_for_object
from backend.player_store import register_player


async def test_oakrun_board_binds_authored_notices_to_stable_object():
    board = get_noticeboard_for_object("oakrun_noticeboard")
    assert board is not None
    assert board.id == "oakrun_crossroads_board"
    assert board.max_player_posts == 20
    assert board.post_ttl_days == 7
    assert len(board.notices) == 3


async def test_player_can_post_one_notice_and_delete_only_their_own(session):
    board = get_noticeboard_for_object("oakrun_noticeboard")
    author = await register_player(session, "pinwriter", "password")
    stranger = await register_player(session, "passerby", "password")
    now = datetime(2042, 3, 5, 12, tzinfo=timezone.utc)

    posted = await post_notice(
        session,
        board,
        player_id=author.id,
        author_name=author.username,
        body="  Meet by the old oak at dusk.  ",
        now=now,
    )
    notices = await list_notices(session, board, author.id, now=now)
    player_notice = next(notice for notice in notices if notice["kind"] == "player")
    assert player_notice["body"] == "Meet by the old oak at dusk."
    assert player_notice["author"] == "pinwriter"
    assert player_notice["can_delete"] is True
    assert player_notice["expires_at"] == (now + timedelta(days=7)).isoformat()

    stranger_view = await list_notices(session, board, stranger.id, now=now)
    assert next(n for n in stranger_view if n["kind"] == "player")["can_delete"] is False
    with pytest.raises(NoticeError, match="only take down your own"):
        await delete_notice(
            session, board, player_id=stranger.id, notice_id=posted.id,
        )

    await delete_notice(session, board, player_id=author.id, notice_id=posted.id)
    after = await list_notices(session, board, author.id, now=now)
    assert all(notice["kind"] == "authored" for notice in after)


async def test_one_active_notice_per_player_then_expiry_releases_slot(session):
    board = get_noticeboard_for_object("oakrun_noticeboard")
    author = await register_player(session, "weeklyposter", "password")
    author_id, author_name = author.id, author.username
    now = datetime(2042, 3, 5, 12, tzinfo=timezone.utc)
    await post_notice(
        session,
        board,
        player_id=author_id,
        author_name=author_name,
        body="First notice",
        now=now,
    )

    with pytest.raises(NoticeError, match="already have a notice"):
        await post_notice(
            session,
            board,
            player_id=author_id,
            author_name=author_name,
            body="Second notice",
            now=now + timedelta(days=1),
        )

    await post_notice(
        session,
        board,
        player_id=author_id,
        author_name=author_name,
        body="A fresh week",
        now=now + timedelta(days=8),
    )
    notices = await list_notices(
        session, board, author_id, now=now + timedelta(days=8),
    )
    player_notices = [notice for notice in notices if notice["kind"] == "player"]
    assert [notice["body"] for notice in player_notices] == ["A fresh week"]


async def test_notice_text_is_bounded(session):
    board = get_noticeboard_for_object("oakrun_noticeboard")
    author = await register_player(session, "longwriter", "password")
    with pytest.raises(NoticeError, match="at most 500"):
        await post_notice(
            session,
            board,
            player_id=author.id,
            author_name=author.username,
            body="x" * 501,
        )
