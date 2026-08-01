"""Läser in ett kundpaket från kommandoraden.

    python -m app.import_customers paket.tar.gz [--ersatt]

Använd --ersatt för att skriva över kunder som redan finns med samma kundnummer.
Utan flaggan hoppas de över.
"""

import asyncio
import sys

from .db import SessionLocal, init_db
from .services.packages import las_in_paket


async def main(sokvag: str, ersatt: bool) -> None:
    await init_db()
    with open(sokvag, "rb") as fh:
        data = fh.read()
    async with SessionLocal() as db:
        r = await las_in_paket(db, data, actor="kommandorad", ersatt=ersatt)
    print(f"  inlästa kunder: {len(r['skapade'])}")
    for namn in r["skapade"]:
        print(f"    {namn}")
    if r["hoppade"]:
        print(f"  hoppade över {len(r['hoppade'])} som redan fanns (kör med --ersatt för att skriva över):")
        for namn in r["hoppade"]:
            print(f"    {namn}")
    print(f"  filer: {r['filer']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1], "--ersatt" in sys.argv))
