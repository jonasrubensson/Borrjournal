"""Löpnummer som inte krockar och inte återanvänds.

Numren räknades tidigare ut från antalet rader. Raderade man en post sjönk
antalet, nästa post fick ett nummer som redan fanns, och sparandet föll på
unikhetskravet. Ett raderat nummer ska inte heller återuppstå: står BES-1004 i
någons anteckningar ska det inte plötsligt vara ett annat besök.

Därför utgår vi från det högsta nummer som finns, och kontrollerar ändå att
kandidaten är ledig innan den används.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Två samtidiga sparningar hinner läsa samma högsta nummer innan någon skrivit,
# och då faller den andra på unikhetskravet. Ett dubbelklick räcker för att
# utlösa det. Låset gör tilldelning och skrivning till ett odelbart steg.
_las = asyncio.Lock()


@asynccontextmanager
async def nummerlas():
    """Håll det här runt både numret och commit, inte bara numret."""
    async with _las:
        yield


async def nummerlas_beroende():
    """Som beroende på en endpoint: låset hålls tills svaret är klart.

    Används där flera numrerade poster skapas i samma transaktion, och det
    därför inte går att linda in bara en enskild commit.
    """
    async with _las:
        yield


async def spara_numrerad(
    db: AsyncSession, obj, model, column, prefix: str, start: int = 1000, forsok: int = 5
):
    """Sparar en post med löpnummer och gör om försöket om numret snuvats.

    Låset räcker inom en process. Retry-slingan finns för att appen ska klara
    flera arbetare eller flera instanser mot samma databas.
    """
    setattr(obj, column.key, await nasta_nummer(db, model, column, prefix, start))
    db.add(obj)
    for runda in range(forsok):
        try:
            await db.commit()
            return obj
        except IntegrityError as exc:
            if "UNIQUE" not in str(getattr(exc, "orig", exc)).upper() or runda == forsok - 1:
                raise
            await db.rollback()
            nytt = await nasta_nummer(db, model, column, prefix, start)
            print(
                f"[borrjournal] {prefix}: numret var upptaget, tar {nytt} i stället",
                flush=True,
            )
            setattr(obj, column.key, nytt)
            db.add(obj)
    return obj


async def nasta_nummer(
    db: AsyncSession, model, column, prefix: str, start: int = 1000
) -> str:
    """Ger nästa lediga nummer i serien, till exempel BES-1042."""
    befintliga = (await db.execute(select(column))).scalars().all()

    hogsta = start
    for varde in befintliga:
        if not varde or not str(varde).startswith(f"{prefix}-"):
            continue
        svans = str(varde)[len(prefix) + 1 :]
        if svans.isdigit():
            hogsta = max(hogsta, int(svans))

    taget = {str(x) for x in befintliga if x}
    kandidat = hogsta + 1
    while f"{prefix}-{kandidat}" in taget:
        kandidat += 1
    return f"{prefix}-{kandidat}"
