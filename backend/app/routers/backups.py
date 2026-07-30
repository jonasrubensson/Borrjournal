import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import BackupRecord, User
from ..security import log_action, require_admin
from ..services import backup as svc
from ..services.notify import DEFAULT_SCHEDULE, SCHEDULE_KEY, get_setting, save_setting

router = APIRouter(prefix="/api/backups", tags=["backup"])


def out(r: BackupRecord) -> dict:
    return {
        "id": r.id,
        "filename": r.filename,
        "size_bytes": r.size_bytes,
        "engine": r.engine,
        "trigger": r.trigger,
        "status": r.status,
        "detail": r.detail,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "created_by": r.created_by,
        "exists": os.path.exists(os.path.join(svc.BACKUP_DIR, r.filename)),
    }


@router.get("")
async def list_backups(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(100))
    ).scalars().all()
    schedule = await get_setting(db, SCHEDULE_KEY, DEFAULT_SCHEDULE)
    usage = await svc.disk_usage(db)
    return {
        "backups": [out(r) for r in rows],
        "schedule": schedule,
        "usage": usage,
        "engine": "pg_dump" if (svc.is_postgres() and svc.pg_dump_available()) else "json",
        "postgres": svc.is_postgres(),
        "pg_dump_available": svc.pg_dump_available(),
    }


@router.post("", status_code=201)
async def create_backup(
    request: Request, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    record = await svc.create_backup(db, trigger="manuell", actor=user.username)
    await log_action(
        db,
        "BACKUP_CREATE" if record.status == "klar" else "BACKUP_FAIL",
        actor=user.username,
        object_type="backup",
        object_id=record.id,
        request=request,
        detail=record.filename if record.status == "klar" else record.detail,
    )
    if record.status != "klar":
        raise HTTPException(status_code=500, detail=record.detail or "Backupen misslyckades")
    return out(record)


@router.get("/schedule")
async def read_schedule(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await get_setting(db, SCHEDULE_KEY, DEFAULT_SCHEDULE)


@router.put("/schedule")
async def write_schedule(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    conf = await get_setting(db, SCHEDULE_KEY, DEFAULT_SCHEDULE)
    for key in ("enabled", "hour", "minute", "keep_days", "reminder_scan_hour"):
        if key in payload:
            conf[key] = payload[key]
    conf["hour"] = max(0, min(23, int(conf["hour"])))
    conf["minute"] = max(0, min(59, int(conf["minute"])))
    conf["reminder_scan_hour"] = max(0, min(23, int(conf["reminder_scan_hour"])))
    conf["keep_days"] = max(1, min(3650, int(conf["keep_days"])))
    await save_setting(db, SCHEDULE_KEY, conf)
    await log_action(db, "BACKUP_SCHEDULE", actor=user.username, request=request, detail=str(conf))
    return conf


@router.get("/{backup_id}/download")
async def download(
    backup_id: str, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    r = (
        await db.execute(select(BackupRecord).where(BackupRecord.id == backup_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Backupen finns inte")
    path = os.path.join(svc.BACKUP_DIR, r.filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="Filen är borta från disk")
    return FileResponse(path, media_type="application/gzip", filename=r.filename)


@router.get("/{backup_id}/restore-guide")
async def restore_guide(
    backup_id: str, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Återställning görs medvetet från terminalen, inte via webben. Här är exakta kommandon."""
    r = (
        await db.execute(select(BackupRecord).where(BackupRecord.id == backup_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Backupen finns inte")

    if r.engine == "pg_dump":
        steps = [
            "docker compose stop app",
            f"docker compose cp app:/data/backups/{r.filename} ./{r.filename}",
            f"tar -xzf {r.filename}",
            "cat db.dump | docker compose exec -T db pg_restore -U borrjournal -d borrjournal --clean --if-exists",
            "docker compose cp files/. app:/data/files/",
            "docker compose start app",
        ]
    else:
        steps = [
            "docker compose stop app",
            f"docker compose cp app:/data/backups/{r.filename} ./{r.filename}",
            f"tar -xzf {r.filename}",
            "docker compose run --rm -v $(pwd):/in app python -m app.restore /in/db.json",
            "docker compose start app",
        ]

    return {
        "filename": r.filename,
        "engine": r.engine,
        "steps": steps,
        "warning": (
            "Återställning skriver över hela databasen. Ta en ny backup först, "
            "och räkna med att alla inloggade sessioner bryts."
        ),
    }


@router.delete("/{backup_id}", status_code=204)
async def delete_backup(
    backup_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    r = (
        await db.execute(select(BackupRecord).where(BackupRecord.id == backup_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Backupen finns inte")
    path = os.path.join(svc.BACKUP_DIR, r.filename)
    if os.path.exists(path):
        os.remove(path)
    await db.delete(r)
    await db.commit()
    await log_action(
        db,
        "BACKUP_DELETE",
        actor=user.username,
        object_type="backup",
        object_id=backup_id,
        request=request,
        detail=r.filename,
    )
