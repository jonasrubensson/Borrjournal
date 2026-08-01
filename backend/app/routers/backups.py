import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import iso_utc
from ..models import BackupRecord, User
from ..security import log_action, require_admin
from ..services import backup as svc
from ..services.notify import DEFAULT_SCHEDULE, SCHEDULE_KEY, get_setting, save_setting

router = APIRouter(prefix="/api/backups", tags=["backup"])


def out(r: BackupRecord) -> dict:
    import json as _json

    try:
        rakning = _json.loads(r.counts or "{}")
    except ValueError:
        rakning = {}
    return {
        "file_count": rakning.get("_filer"),
        "file_bytes": rakning.get("_filbytes"),
        "id": r.id,
        "filename": r.filename,
        "size_bytes": r.size_bytes,
        "engine": r.engine,
        "trigger": r.trigger,
        "status": r.status,
        "detail": r.detail,
        "created_at": iso_utc(r.created_at) if r.created_at else None,
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
        "backup_dir": svc.BACKUP_DIR,
        "backup_dir_extern": bool(getattr(svc.settings, "backup_dir", "")),
        "engine": "pg_dump" if (svc.is_postgres() and svc.pg_dump_available()) else "json",
        "postgres": svc.is_postgres(),
        "pg_dump_available": svc.pg_dump_available(),
    }


@router.post("", status_code=201)
async def create_backup(
    request: Request,
    payload: dict | None = None,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    med_filer = True if payload is None else bool(payload.get("include_files", True))
    record = await svc.create_backup(
        db, trigger="manuell", actor=user.username, include_files=med_filer
    )
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


@router.get("/packages/export")
async def export_customers(
    customer_ids: str = "",
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Kundpaket med allt: uppgifter, journal, dokument och bilder.

    Utan customer_ids exporteras hela registret. Paketet är fristående och kan
    läsas in i ett annat system utan att röra det som redan finns där.
    """
    from datetime import date

    from fastapi.responses import Response

    from ..models import Customer
    from ..services.packages import bygg_paket

    if customer_ids.strip():
        ids = [x.strip() for x in customer_ids.split(",") if x.strip()]
    else:
        ids = list((await db.execute(select(Customer.id))).scalars().all())

    if not ids:
        raise HTTPException(status_code=404, detail="Inga kunder att exportera")

    data = await bygg_paket(db, ids)
    namn = (
        f"borrjournal-kunder-{date.today().isoformat()}.tar.gz"
        if len(ids) > 1
        else f"borrjournal-kund-{date.today().isoformat()}.tar.gz"
    )
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{namn}"'},
    )


@router.post("/packages/import")
async def import_customers(
    request: Request,
    file: UploadFile = File(...),
    replace: bool = Form(False),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Läser in ett kundpaket. Rör inte kunder som inte finns i paketet."""
    from ..services.packages import las_in_paket

    data = await file.read()
    if len(data) > 500 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Paketet är större än 500 MB")
    try:
        resultat = await las_in_paket(db, data, actor=user.username, ersatt=replace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Inläsningen misslyckades: {exc}") from exc

    await log_action(
        db,
        "PACKAGE_IMPORT",
        actor=user.username,
        request=request,
        detail=f"{len(resultat['skapade'])} kunder, {resultat['filer']} filer",
    )
    return resultat


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
