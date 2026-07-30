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

BACKUP_DIR = os.path.join(settings.data_dir, "backups")
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
                    item[key] = value.isoformat()
                else:
                    item[key] = value
            serialised.append(item)
        payload[table.name] = serialised
        counts[table.name] = len(serialised)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return "json", counts


async def create_backup(
    db: AsyncSession, *, trigger: str = "manuell", actor: str = ""
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

            manifest = {
                "app": "borrjournal",
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "engine": engine,
                "database_member": db_member,
                "trigger": trigger,
                "created_by": actor or "system",
                "counts": counts,
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
                if os.path.isdir(FILE_DIR):
                    tar.add(FILE_DIR, arcname="files")
                if os.path.isdir(THUMB_DIR):
                    tar.add(THUMB_DIR, arcname="thumbs")

        record.engine = engine
        record.size_bytes = os.path.getsize(final_path)
        record.counts = json.dumps(counts, ensure_ascii=False)
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
