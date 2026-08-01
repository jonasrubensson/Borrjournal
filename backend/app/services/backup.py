"""Backup av databas och filarkiv till en enda tar.gz.

Två motorer:
  pg_dump  - används mot Postgres. Ger en dump som går att läsa in med pg_restore utan appen.
  json     - fallback (t.ex. SQLite i utveckling). Logisk dump av alla tabeller.

Arkivet innehåller alltid manifest.json, så att en framtida version vet vad den läser.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import Base
from ..models import BackupRecord
from ..schemas import iso_utc

BACKUP_DIR = settings.backup_dir or os.path.join(settings.data_dir, "backups")
FILE_DIR = os.path.join(settings.data_dir, "files")
THUMB_DIR = os.path.join(settings.data_dir, "thumbs")

# Kolumner som aldrig får hamna i en backup som laddas ner av en människa
REDACT: dict[str, set[str]] = {}


def is_postgres() -> bool:
    return settings.database_url.startswith("postgresql")


def pg_dump_available() -> bool:
    return shutil.which("pg_dump") is not None


def _pg_parts() -> dict:
    u = urlparse(settings.database_url.replace("+asyncpg", ""))
    return {
        "host": u.hostname or "localhost",
        "port": str(u.port or 5432),
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "db": (u.path or "/").lstrip("/"),
    }


async def _dump_postgres(target: str) -> str:
    p = _pg_parts()
    env = {**os.environ, "PGPASSWORD": p["password"]}
    proc = await asyncio.create_subprocess_exec(
        "pg_dump", "-h", p["host"], "-p", p["port"], "-U", p["user"],
        "-d", p["db"], "-Fc", "-f", target,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump misslyckades: {err.decode(errors='replace')[:400]}")
    return "pg_dump"


async def _dump_json(db: AsyncSession, target: str) -> tuple[str, dict]:
    payload: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        rows = (await db.execute(select(table))).mappings().all()
        serialised = []
        for row in rows:
            item = {}
            for key, value in dict(row).items():
                if isinstance(value, datetime):
                    item[key] = iso_utc(value)
                else:
                    item[key] = value
            serialised.append(item)
        payload[table.name] = serialised
        counts[table.name] = len(serialised)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return "json", counts


def _lasbart_namn(kund: str, filnamn: str, stored: str) -> str:
    """Namn i arkivet som går att förstå utan att öppna databasen.

    Filerna lagras med slumpade namn på disk för att undvika krockar, men i en
    backup vill man kunna se vad som är vad utan att läsa db.json.
    """
    trygg = lambda t: "".join(c if c.isalnum() or c in " .-_()" else "_" for c in t).strip()[:80]  # noqa: E731
    return f"filer/{trygg(kund) or 'okand-kund'}/{stored[:8]}-{trygg(filnamn) or 'fil'}"


async def _filkarta(db: AsyncSession) -> dict:
    """stored_name -> läsbar sökväg i arkivet."""
    from ..models import Customer, StoredFile

    rader = (
        await db.execute(
            select(StoredFile.stored_name, StoredFile.filename, Customer.name)
            .join(Customer, Customer.id == StoredFile.customer_id, isouter=True)
        )
    ).all()
    return {r[0]: _lasbart_namn(r[2] or "", r[1], r[0]) for r in rader}


async def create_backup(
    db: AsyncSession,
    *,
    trigger: str = "manuell",
    actor: str = "",
    include_files: bool = True,
) -> BackupRecord:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    filename = f"borrjournal-{stamp}.tar.gz"
    # Två backuper inom samma sekund får inte krocka på filnamnet
    suffix = 1
    while os.path.exists(os.path.join(BACKUP_DIR, filename)):
        suffix += 1
        filename = f"borrjournal-{stamp}-{suffix}.tar.gz"
    final_path = os.path.join(BACKUP_DIR, filename)

    record = BackupRecord(filename=filename, trigger=trigger, created_by=actor or "system")
    counts: dict = {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if is_postgres() and pg_dump_available():
                inner = os.path.join(tmp, "db.dump")
                engine = await _dump_postgres(inner)
                db_member = "db.dump"
            else:
                inner = os.path.join(tmp, "db.json")
                engine, counts = await _dump_json(db, inner)
                db_member = "db.json"

            # Räkna filerna först, så att manifestet stämmer med innehållet
            karta = await _filkarta(db) if include_files else {}
            att_ta_med = []
            if include_files and os.path.isdir(FILE_DIR):
                for namn in sorted(os.listdir(FILE_DIR)):
                    kalla = os.path.join(FILE_DIR, namn)
                    if os.path.isfile(kalla):
                        att_ta_med.append((kalla, namn, os.path.getsize(kalla)))
            antal_filer = len(att_ta_med)
            filbytes = sum(x[2] for x in att_ta_med)

            manifest = {
                "app": "borrjournal",
                "version": 2,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "engine": engine,
                "database_member": db_member,
                "trigger": trigger,
                "created_by": actor or "system",
                "counts": counts,
                "files_included": include_files,
                "file_count": antal_filer,
                "file_bytes": filbytes,
                "innehall": (
                    "Katalogen 'filer' innehåller uppladdade dokument och bilder med läsbara "
                    "namn, grupperade per kund. Katalogen 'files' innehåller samma filer med de "
                    "lagrade namnen, och är den som används vid återläsning."
                ),
                "restore": (
                    "pg_restore -h <host> -U borrjournal -d borrjournal --clean --if-exists db.dump"
                    if engine == "pg_dump"
                    else "Läses in med app-kommandot: python -m app.restore db.json"
                ),
            }
            manifest_path = os.path.join(tmp, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2)

            with tarfile.open(final_path, "w:gz") as tar:
                tar.add(inner, arcname=db_member)
                tar.add(manifest_path, arcname="manifest.json")
                for kalla, namn, _storlek in att_ta_med:
                    # Läsbart namn, grupperat per kund
                    tar.add(kalla, arcname=karta.get(namn, f"filer/losa/{namn}"))
                if att_ta_med:
                    # Originalnamnen behövs för att kunna läsa tillbaka maskinellt
                    tar.add(FILE_DIR, arcname="files")
                    if os.path.isdir(THUMB_DIR):
                        tar.add(THUMB_DIR, arcname="thumbs")

        record.engine = engine
        record.size_bytes = os.path.getsize(final_path)
        counts["_filer"] = antal_filer
        counts["_filbytes"] = filbytes
        record.counts = json.dumps(counts, ensure_ascii=False)
        record.detail = (
            f"{antal_filer} filer" if include_files else "utan filer, endast databas"
        )
        record.status = "klar"
    except Exception as exc:  # noqa: BLE001 - felet ska synas i listan, inte krascha jobbet
        record.status = "fel"
        record.detail = str(exc)[:1000]
        if os.path.exists(final_path):
            os.remove(final_path)

    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def prune(db: AsyncSession, keep_days: int, keep_min: int = 3) -> int:
    """Tar bort dumpar äldre än keep_days, men behåller alltid de senaste keep_min."""
    rows = (
        await db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()))
    ).scalars().all()
    removed = 0
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    for index, record in enumerate(rows):
        if index < keep_min or record.status != "klar":
            continue
        created = record.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created.timestamp() < cutoff:
            path = os.path.join(BACKUP_DIR, record.filename)
            if os.path.exists(path):
                os.remove(path)
            await db.delete(record)
            removed += 1
    if removed:
        await db.commit()
    return removed


async def disk_usage(db: AsyncSession) -> dict:
    total = (await db.execute(select(func.sum(BackupRecord.size_bytes)))).scalar() or 0
    free = shutil.disk_usage(settings.data_dir).free if os.path.isdir(settings.data_dir) else 0
    return {"backup_bytes": int(total), "free_bytes": int(free)}
