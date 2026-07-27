"""Mapper between `npcs` rows (at rest) and NPC entities (in memory).

The individual-persistence twin of room_loader: that module maps template
data (read-only at play time), this one maps instance state (play edits it).
Load on room entry, save on eviction/shutdown — the DB stays at the edges,
never in the hot loop.

Eviction saving is what kills room reset for individuals (NPCS.md Decision
10): fungible enemies are still deliberately forgotten, NPCs are not.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.actor_defs import get_actor_art
from backend.config import NPC_TRANSCRIPT_LIMIT
from backend.entities import NPC, Disposition, Position
from backend.models import NPCRow
from backend.persona import validate_persona


def _entity_id(db_id: int) -> str:
    # "npc_" prefix — get_entity dispatches on it, like player_/enemy_. The
    # numeric part is the DB id, so the runtime id is stable across loads.
    return f"npc_{db_id}"


def _validate_row_identity(row: NPCRow) -> None:
    """Validate both the persona payload and its stable authored identity."""
    validate_persona(row.persona)
    persona_id = row.persona["id"]
    if row.content_id is not None and row.content_id != persona_id:
        raise ValueError(
            f"NPC row {row.id} identity mismatch: "
            f"content_id={row.content_id!r}, persona.id={persona_id!r}"
        )


async def get_npc_row_by_content_id(
    session: AsyncSession, content_id: str,
) -> NPCRow | None:
    """Resolve story identity without depending on a replaceable numeric id."""
    row = (await session.execute(
        select(NPCRow).where(NPCRow.content_id == content_id)
    )).scalar_one_or_none()
    if row is not None:
        _validate_row_identity(row)
    return row


async def load_npcs(session: AsyncSession, room_id: int) -> list[NPC]:
    """All individuals currently in a room, personas re-validated on the way
    in (the gate runs on load as well as insert, so a row edited by hand or a
    future generator can't smuggle a malformed persona into the world)."""
    rows = (await session.execute(
        select(NPCRow)
        .where(NPCRow.room_id == room_id)
        .order_by(NPCRow.content_id, NPCRow.id)
    )).scalars().all()

    npcs = []
    for row in rows:
        _validate_row_identity(row)
        art = get_actor_art(row.persona.get("art_id"))
        npcs.append(NPC(
            id=_entity_id(row.id),
            db_id=row.id,
            name=row.name,
            position=Position(row.x, row.y),
            hp=row.hp,
            max_hp=row.max_hp,
            defense=row.defense,
            attack_damage=row.attack_damage,
            is_alive=row.is_alive,
            disposition=Disposition(row.disposition),
            persona=row.persona,
            transcript=list(row.memory or [])[-NPC_TRANSCRIPT_LIMIT:],
            party_owner_id=row.party_owner_id,
            image=art.image if art else None,
            visual_size=art.visual_size if art else (1, 1),
        ))
    return npcs


async def save_npcs(
    session: AsyncSession,
    npcs: list[NPC],
    room_id: int | None = None,
    *,
    commit: bool = True,
) -> None:
    """Write live NPC state back to rows.

    Normal edge saves commit the complete group. A caller adding causal
    records in the same transaction can request a flush instead.
    """
    for npc in npcs:
        row = await session.get(NPCRow, npc.db_id)
        if row is None:
            # The row vanished under us (hand-deleted db?). Losing one NPC
            # must not abort saving the rest.
            continue
        if room_id is not None:
            row.room_id = room_id
        row.x = npc.position.x
        row.y = npc.position.y
        row.hp = npc.hp
        row.max_hp = npc.max_hp
        row.defense = npc.defense
        row.attack_damage = npc.attack_damage
        row.is_alive = npc.is_alive
        row.disposition = npc.disposition.value
        row.memory = list(npc.transcript)[-NPC_TRANSCRIPT_LIMIT:]
        row.party_owner_id = npc.party_owner_id
    if commit:
        await session.commit()
    else:
        await session.flush()
