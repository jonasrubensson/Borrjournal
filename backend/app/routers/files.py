import io
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ALLOWED_TYPES, settings
from ..db import get_db
from ..models import Customer, StoredFile, User
from ..schemas import file_out
from ..security import current_user, log_action, require_write

router = APIRouter(prefix="/api", tags=["filer"])

FILE_DIR = os.path.join(settings.data_dir, "files")
THUMB_DIR = os.path.join(settings.data_dir, "thumbs")


def ensure_dirs() -> None:
    os.makedirs(FILE_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)


def _save_thumb(img: Image.Image, stored_name: str) -> str:
    img.thumbnail((640, 640))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    name = f"{stored_name}.thumb.jpg"
    img.save(os.path.join(THUMB_DIR, name), "JPEG", quality=82)
    return name


def make_thumb(raw: bytes, stored_name: str) -> str | None:
    try:
        return _save_thumb(Image.open(io.BytesIO(raw)), stored_name)
    except (UnidentifiedImageError, OSError):
        return None


def make_pdf_thumb(raw: bytes, stored_name: str) -> str | None:
    """Första sidan som tumnagel. Ett borrprotokoll går att känna igen på håll."""
    try:
        import pypdfium2

        pdf = pypdfium2.PdfDocument(raw)
        if len(pdf) == 0:
            return None
        page = pdf[0]
        bitmap = page.render(scale=1.4)
        return _save_thumb(bitmap.to_pil(), stored_name)
    except Exception:  # noqa: BLE001 - en trasig PDF får inte stoppa uppladdningen
        return None


@router.post("/customers/{customer_id}/files", status_code=201)
async def upload(
    customer_id: str,
    request: Request,
    file: UploadFile = File(...),
    facility_id: str | None = Form(None),
    journal_id: str | None = Form(None),
    caption: str = Form(""),
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    exists = (await db.execute(select(Customer.id).where(Customer.id == customer_id))).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Kunden finns inte")

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Filtypen stöds inte. Ladda upp PDF, DOCX, XLSX eller bild.",
        )

    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413, detail=f"Filen är större än {settings.max_upload_mb} MB"
        )
    if not raw:
        raise HTTPException(status_code=400, detail="Filen är tom")

    ensure_dirs()
    kind, default_ext = ALLOWED_TYPES[content_type]
    ext = os.path.splitext(file.filename or "")[1] or default_ext
    stored_name = f"{uuid.uuid4()}{ext.lower()}"
    with open(os.path.join(FILE_DIR, stored_name), "wb") as fh:
        fh.write(raw)

    if kind == "bild":
        thumb_name = make_thumb(raw, stored_name)
    elif content_type == "application/pdf":
        thumb_name = make_pdf_thumb(raw, stored_name)
    else:
        thumb_name = None

    record = StoredFile(
        customer_id=customer_id,
        facility_id=facility_id or None,
        journal_id=journal_id or None,
        filename=os.path.basename(file.filename or stored_name),
        stored_name=stored_name,
        thumb_name=thumb_name,
        content_type=content_type,
        kind=kind,
        size_bytes=len(raw),
        caption=caption,
        uploaded_by=user.full_name or user.username,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    await log_action(
        db,
        "FILE_UPLOAD",
        actor=user.username,
        object_type="file",
        object_id=record.id,
        request=request,
        detail=record.filename,
    )
    return file_out(record)


@router.get("/customers/{customer_id}/files")
async def list_files(
    customer_id: str,
    kind: str | None = None,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(StoredFile)
        .where(StoredFile.customer_id == customer_id)
        .order_by(StoredFile.uploaded_at.desc())
    )
    if kind:
        stmt = stmt.where(StoredFile.kind == kind)
    return [file_out(f) for f in (await db.execute(stmt)).scalars().all()]


async def fetch_file(db: AsyncSession, file_id: str) -> StoredFile:
    f = (await db.execute(select(StoredFile).where(StoredFile.id == file_id))).scalar_one_or_none()
    if f is None:
        raise HTTPException(status_code=404, detail="Filen finns inte")
    return f


@router.get("/files/{file_id}")
async def download(
    file_id: str,
    inline: bool = True,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    f = await fetch_file(db, file_id)
    path = os.path.join(FILE_DIR, f.stored_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="Filen saknas på disk")
    disposition = "inline" if inline else "attachment"
    return FileResponse(
        path,
        media_type=f.content_type or "application/octet-stream",
        filename=f.filename,
        content_disposition_type=disposition,
    )


@router.get("/files/{file_id}/thumb")
async def thumb(file_id: str, _: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    f = await fetch_file(db, file_id)
    if not f.thumb_name:
        raise HTTPException(status_code=404, detail="Ingen tumnagel finns")
    path = os.path.join(THUMB_DIR, f.thumb_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="Tumnageln saknas på disk")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(
    file_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    f = await fetch_file(db, file_id)
    for path in (
        os.path.join(FILE_DIR, f.stored_name),
        os.path.join(THUMB_DIR, f.thumb_name) if f.thumb_name else None,
    ):
        if path and os.path.exists(path):
            os.remove(path)
    await db.delete(f)
    await db.commit()
    await log_action(
        db,
        "FILE_DELETE",
        actor=user.username,
        object_type="file",
        object_id=file_id,
        request=request,
        detail=f.filename,
    )


@router.get("/files")
async def search_files(
    q: str | None = None,
    kind: str | None = None,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(StoredFile, Customer)
        .join(Customer, Customer.id == StoredFile.customer_id)
        .order_by(StoredFile.uploaded_at.desc())
        .limit(300)
    )
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(StoredFile.filename).like(needle),
                func.lower(StoredFile.caption).like(needle),
                func.lower(Customer.name).like(needle),
            )
        )
    if kind:
        stmt = stmt.where(StoredFile.kind == kind)
    rows = (await db.execute(stmt)).unique().all()
    out = []
    for f, customer in rows:
        item = file_out(f)
        item["customer"] = {"id": customer.id, "name": customer.name}
        out.append(item)
    return out
