"""Systemhändelser: fel som inte har någon användare att svara.

Ett misslyckat koordinatuppslag i bakgrunden, ett SGU-hämtning som inte gick
igenom, ett utskick som fastnade. Utan en synlig lista blir sådant en rad i
containerloggen som ingen läser.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SystemEvent

MAX_HANDELSER = 500


async def logga(
    db: AsyncSession,
    *,
    level: str = "fel",
    source: str = "",
    message: str,
    detail: str = "",
    object_type: str = "",
    object_id: str = "",
    reference: str = "",
    commit: bool = True,
) -> SystemEvent:
    handelse = SystemEvent(
        level=level,
        source=source[:40],
        message=message[:500],
        detail=detail[:4000],
        object_type=object_type[:30],
        object_id=(object_id or "")[:36],
        reference=reference[:12],
    )
    db.add(handelse)
    if commit:
        await db.commit()
    print(f"[borrjournal] {level.upper()} {source}: {message}", flush=True)
    return handelse


async def stada(db: AsyncSession) -> int:
    """Behåller de senaste, så att tabellen inte växer i oändlighet."""
    ider = (
        await db.execute(
            select(SystemEvent.id).order_by(SystemEvent.at.desc()).offset(MAX_HANDELSER)
        )
    ).scalars().all()
    if not ider:
        return 0
    await db.execute(delete(SystemEvent).where(SystemEvent.id.in_(ider)))
    await db.commit()
    return len(ider)
