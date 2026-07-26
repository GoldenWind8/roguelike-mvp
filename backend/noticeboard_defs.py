"""Authored noticeboard catalogue.

Placed room objects opt into the generic ``noticeboard`` interaction. This
catalogue binds a stable object id to its persistent player-post policy and
the hand-authored notices that always appear above player messages.
"""
from dataclasses import dataclass

from backend.content import load_catalog


@dataclass(frozen=True)
class AuthoredNotice:
    id: str
    author: str
    body: str


@dataclass(frozen=True)
class NoticeboardDefinition:
    id: str
    object_id: str
    label: str
    max_player_posts: int
    post_ttl_days: int
    notices: tuple[AuthoredNotice, ...]


def _required_text(value, what: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{what} must be a non-empty string")
    clean = value.strip()
    if len(clean) > maximum:
        raise RuntimeError(f"{what} exceeds {maximum} characters")
    return clean


def _definition(entry: dict) -> NoticeboardDefinition:
    board_id = _required_text(entry.get("id"), "noticeboard id", maximum=80)
    object_id = _required_text(
        entry.get("object_id"), f"noticeboard {board_id!r} object_id", maximum=120,
    )
    label = _required_text(
        entry.get("label"), f"noticeboard {board_id!r} label", maximum=80,
    )
    max_posts = entry.get("max_player_posts")
    ttl_days = entry.get("post_ttl_days")
    if not isinstance(max_posts, int) or not 1 <= max_posts <= 100:
        raise RuntimeError(f"noticeboard {board_id!r} needs max_player_posts 1..100")
    if not isinstance(ttl_days, int) or not 1 <= ttl_days <= 90:
        raise RuntimeError(f"noticeboard {board_id!r} needs post_ttl_days 1..90")

    raw_notices = entry.get("notices", [])
    if not isinstance(raw_notices, list):
        raise RuntimeError(f"noticeboard {board_id!r} notices must be a list")
    notices: list[AuthoredNotice] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_notices):
        if not isinstance(raw, dict):
            raise RuntimeError(f"noticeboard {board_id!r} notice {index} must be an object")
        notice_id = _required_text(
            raw.get("id"), f"noticeboard {board_id!r} notice id", maximum=80,
        )
        if notice_id in seen:
            raise RuntimeError(f"noticeboard {board_id!r} repeats notice {notice_id!r}")
        seen.add(notice_id)
        notices.append(AuthoredNotice(
            id=notice_id,
            author=_required_text(
                raw.get("author"), f"notice {notice_id!r} author", maximum=60,
            ),
            body=_required_text(
                raw.get("body"), f"notice {notice_id!r} body", maximum=500,
            ),
        ))

    return NoticeboardDefinition(
        id=board_id,
        object_id=object_id,
        label=label,
        max_player_posts=max_posts,
        post_ttl_days=ttl_days,
        notices=tuple(notices),
    )


_BY_ID = {
    board_id: _definition(entry)
    for board_id, entry in load_catalog("noticeboards.json").items()
}
_BY_OBJECT = {definition.object_id: definition for definition in _BY_ID.values()}
if len(_BY_OBJECT) != len(_BY_ID):
    raise RuntimeError("noticeboards.json repeats an object_id")


def get_noticeboard(board_id: str) -> NoticeboardDefinition | None:
    return _BY_ID.get(board_id)


def get_noticeboard_for_object(object_id: str) -> NoticeboardDefinition | None:
    return _BY_OBJECT.get(object_id)
