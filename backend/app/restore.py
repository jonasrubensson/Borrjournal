"""Läser in en JSON-backup. Körs medvetet från kommandoraden, inte från webben.

    python -m app.restore /sokvag/db.json

Skriver över allt innehåll i tabellerna. Ta en ny backup innan du kör.
"""

import asyncio
import json
import sys
from datetime import datetime

from sqlalchemy import delete, insert

from .db import Base, SessionLocal, init_db


def parse(value):
    if isinstance(value, str) and len(value) >= 19 and value[4] == "-" and "T" in value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


async def restore(path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)

    await init_db()
    tables = Base.metadata.sorted_tables

    async with SessionLocal() as db:
        # Töm i omvänd beroendeordning, fyll i rätt ordning
        for table in reversed(tables):
            await db.execute(delete(table))
        for table in tables:
            rows = payload.get(table.name) or []
            if not rows:
                continue
            columns = {c.name for c in table.columns}
            cleaned = [
                {k: parse(v) for k, v in row.items() if k in columns} for row in rows
            ]
            await db.execute(insert(table), cleaned)
            print(f"  {table.name}: {len(cleaned)} rader")
        await db.commit()
    print("Återläsning klar.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    asyncio.run(restore(sys.argv[1]))
