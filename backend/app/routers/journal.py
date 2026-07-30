from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Customer, JournalEntry, Reminder, StoredFile, User
from ..schemas import JournalIn, journal_out
from ..security import current_user, log_action, require_write

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
