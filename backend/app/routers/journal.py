from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Customer, JournalEntry, Reminder, StoredFile, User
from ..schemas import JournalIn, journal_out
from ..security import current_user, log_action, require_admin, require_write

router = APIRouter(prefix="/api", tags=["journal"])


async def attachments(db: AsyncSession, entries: list[JournalEntry]) -> dict[str, list[StoredFile]]:
    ids = [e.id for e in entries]
    if not ids:
        return {}
    files = (
        await db.execute(select(StoredFile).where(StoredFile.journal_id.in_(ids)))
    ).scalars().all()
    grouped: dict[str, list[StoredFile]] = {}
    for f in files:
        grouped.setdefault(f.journal_id, []).append(f)
    return grouped


@router.get("/customers/{customer_id}/journal")
async def customer_journal(
    customer_id: str, _: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    entries = (
        await db.execute(
            select(JournalEntry)
            .where(JournalEntry.customer_id == customer_id)
            .order_by(JournalEntry.created_at.desc())
        )
    ).scalars().all()
    grouped = await attachments(db, list(entries))
    return [journal_out(e, grouped.get(e.id, [])) for e in entries]


@router.post("/customers/{customer_id}/journal", status_code=201)
async def add_entry(
    customer_id: str,
    payload: JournalIn,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(select(Customer.id).where(Customer.id == customer_id))
    ).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Kunden finns inte")
    if not payload.title.strip() and not payload.body.strip():
        raise HTTPException(status_code=400, detail="Skriv en rubrik eller en anteckning")

    # Allt ska kunna spåras till en anläggning. Har kunden minst en måste en väljas,
    # annars blir journalen omöjlig att följa när kunden har flera brunnar.
    from ..models import Facility

    facilities = (
        await db.execute(select(Facility.id).where(Facility.customer_id == customer_id))
    ).scalars().all()
    if facilities:
        if not payload.facility_id:
            raise HTTPException(
                status_code=400, detail="Välj vilken anläggning anteckningen gäller"
            )
        if payload.facility_id not in facilities:
            raise HTTPException(
                status_code=400, detail="Anläggningen hör inte till den här kunden"
            )

    entry = JournalEntry(
        customer_id=customer_id,
        facility_id=payload.facility_id,
        entry_type=payload.entry_type,
        title=payload.title.strip() or "(utan rubrik)",
        body=payload.body.strip(),
        corrects_id=payload.corrects_id,
        author_id=user.id,
        author_name=user.full_name or user.username,
    )
    db.add(entry)
    await db.flush()

    if payload.followup_date:
        db.add(
            Reminder(
                customer_id=customer_id,
                facility_id=payload.facility_id,
                journal_id=entry.id,
                kind="uppfoljning",
                title=payload.followup_title.strip() or f"Uppföljning: {entry.title}",
                body=entry.body[:500],
                due_date=payload.followup_date,
                notify_days_before=0,
                created_by=user.full_name or user.username,
            )
        )

    await db.commit()
    await db.refresh(entry)
    await log_action(
        db,
        "JOURNAL_ADD",
        actor=user.username,
        object_type="journal",
        object_id=entry.id,
        request=request,
        detail=entry.title,
    )
    return journal_out(entry)


@router.get("/journal")
async def all_entries(
    q: str | None = None,
    entry_type: str | None = None,
    limit: int = 200,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(JournalEntry, Customer)
        .join(Customer, Customer.id == JournalEntry.customer_id)
        .order_by(JournalEntry.created_at.desc())
        .limit(min(limit, 1000))
    )
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(JournalEntry.title).like(needle),
                func.lower(JournalEntry.body).like(needle),
                func.lower(JournalEntry.entry_type).like(needle),
                func.lower(Customer.name).like(needle),
            )
        )
    if entry_type:
        stmt = stmt.where(JournalEntry.entry_type == entry_type)

    rows = (await db.execute(stmt)).unique().all()
    entries = [r[0] for r in rows]
    grouped = await attachments(db, entries)
    out = []
    for entry, customer in rows:
        item = journal_out(entry, grouped.get(entry.id, []))
        item["customer"] = {"id": customer.id, "name": customer.name, "customer_no": customer.customer_no}
        out.append(item)
    return out


@router.patch("/journal/{entry_id}")
async def retract_entry(
    entry_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Drar tillbaka en anteckning. Texten står kvar men märks som struken.

    Journalen är avsiktligt inte redigerbar. En anteckning som visar sig vara fel
    stryks och ersätts av en ny, så att det går att se vad som stod och vem som
    ändrade sig. Det är hela poängen med en journal.
    """
    from datetime import datetime, timezone

    entry = (
        await db.execute(select(JournalEntry).where(JournalEntry.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Anteckningen finns inte")

    if payload.get("undo"):
        entry.retracted_at = None
        entry.retracted_by = ""
        entry.retraction_reason = ""
        action = "JOURNAL_UNRETRACT"
    else:
        entry.retracted_at = datetime.now(timezone.utc)
        entry.retracted_by = user.full_name or user.username
        entry.retraction_reason = (payload.get("reason") or "").strip()[:255]
        action = "JOURNAL_RETRACT"

    await db.commit()
    await db.refresh(entry)
    await log_action(
        db,
        action,
        actor=user.username,
        object_type="journal",
        object_id=entry.id,
        request=request,
        detail=entry.retraction_reason,
    )
    return journal_out(entry)


@router.delete("/journal/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Radera på riktigt. Endast administratör, för rena felinmatningar."""
    entry = (
        await db.execute(select(JournalEntry).where(JournalEntry.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Anteckningen finns inte")
    title = entry.title
    await db.delete(entry)
    await db.commit()
    await log_action(
        db,
        "JOURNAL_DELETE",
        actor=user.username,
        object_type="journal",
        object_id=entry_id,
        request=request,
        detail=title,
    )
